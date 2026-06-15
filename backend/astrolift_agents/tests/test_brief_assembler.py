"""Tests for the GitHub -> Brief assembly service (#41).

Runs against real Postgres (pytest-django) and exercises the real zip/TOML
parse path against an in-memory zipball. Only the network boundary
(``requests.get``) is mocked — never the database.
"""

from __future__ import annotations

import io
import zipfile

import pytest
import requests

from astrolift_agents.models import Brief
from astrolift_agents.services import brief_assembler
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Silence the User -> Profile -> OpenSearch indexing chain on org create."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture(autouse=True)
def _no_blob_store(monkeypatch):
    """Default: no blob store configured, so storage degrades to empty key.

    Individual tests that exercise the storage path override this.
    """
    from astrolift_pipelines.artifact_store import BlobStoreNotConfiguredError

    def _raise(_org):
        raise BlobStoreNotConfiguredError("no blob store in test")

    monkeypatch.setattr("astrolift_pipelines.artifact_store._get_blob_driver", _raise)


@pytest.fixture
def org():
    return Organization.objects.create(name="Brief Org", slug="brief-asm-org")


# ---------------------------------------------------------------------------
# Zipball / response helpers
# ---------------------------------------------------------------------------

_FULL_TOML = b"""
[skills.reviewer]
system_prompt = "You review pull requests."
tools = ["read_file", "comment"]

[environment]
LOG_LEVEL = "debug"
REGION = "us-west-2"
tool_preset = "code-review"
allow_install = true

[secrets]
OPENAI_API_KEY = { secret_name = "arn:aws:secretsmanager:openai" }
GITHUB_TOKEN = "vault://secret/gh-token"
"""


def _make_zipball(toml_bytes: bytes | None, *, top_dir: str = "owner-repo-abc123") -> bytes:
    """Build a GitHub-shaped zipball (single top-level dir) in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top_dir}/README.md", "hello")
        if toml_bytes is not None:
            zf.writestr(f"{top_dir}/astrolift.toml", toml_bytes)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes, *, status_ok: bool = True):
        self.content = content
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError("404 Not Found")


def _patch_get(monkeypatch, response: _FakeResponse, capture: dict | None = None):
    def _fake_get(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture["kwargs"] = kwargs
        return response

    monkeypatch.setattr(brief_assembler.requests, "get", _fake_get)


# ---------------------------------------------------------------------------
# Fresh assembly
# ---------------------------------------------------------------------------


def test_assembles_ready_brief_with_parsed_manifest(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)))

    brief = brief_assembler.assemble_agent_brief(
        organization=org,
        config_repo="owner/repo",
        config_branch="main",
        context={"task": "t-1"},
    )

    assert brief.pk is not None
    assert brief.status == Brief.Status.READY
    assert brief.organization_id == org.id
    assert brief.assembled_at is not None
    assert len(brief.content_hash) == 64
    assert brief.ttl_seconds == 3600

    manifest = brief.manifest_snapshot
    assert manifest["skill_slug"] == "reviewer"
    assert manifest["system_prompt"] == "You review pull requests."
    assert manifest["tools"] == ["read_file", "comment"]
    # Reserved keys are pulled out of env_vars and surfaced structurally.
    assert manifest["env_vars"] == {"LOG_LEVEL": "debug", "REGION": "us-west-2"}
    assert manifest["tool_preset"] == "code-review"
    assert manifest["allow_install"] is True


def test_secrets_refs_store_uri_not_value(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)))

    brief = brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/repo")

    refs = {r["env_var"]: r["uri"] for r in brief.secrets_refs}
    # Table form resolves the secret_name; shorthand string form is taken as-is.
    assert refs["OPENAI_API_KEY"] == "arn:aws:secretsmanager:openai"
    assert refs["GITHUB_TOKEN"] == "vault://secret/gh-token"


def test_storage_key_empty_when_blob_store_unconfigured(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)))

    brief = brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/repo")

    # _no_blob_store fixture makes the driver unavailable: degrade gracefully.
    assert brief.storage_key == ""


# ---------------------------------------------------------------------------
# Dedup / content addressing
# ---------------------------------------------------------------------------


def test_identical_inputs_dedup_to_same_brief(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)))

    first = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo", context={"task": "t-1"}
    )
    second = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo", context={"task": "t-1"}
    )

    assert first.pk == second.pk
    assert Brief.objects.filter(organization=org).count() == 1


def test_different_context_produces_distinct_brief(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)))

    first = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo", context={"task": "t-1"}
    )
    second = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo", context={"task": "t-2"}
    )

    assert first.pk != second.pk
    assert first.content_hash != second.content_hash
    assert Brief.objects.filter(organization=org).count() == 2


def test_different_zip_content_produces_distinct_brief(monkeypatch, org):
    # First assembly with one bundle.
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)))
    first = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo", context={"task": "t-1"}
    )

    # Same context, different bundle bytes -> different hash, new Brief.
    altered = _FULL_TOML.replace(b"us-west-2", b"eu-central-1")
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(altered)))
    second = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo", context={"task": "t-1"}
    )

    assert first.content_hash != second.content_hash
    assert Brief.objects.filter(organization=org).count() == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_repo_without_manifest_still_assembles(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(None)))

    brief = brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/repo")

    assert brief.status == Brief.Status.READY
    assert brief.manifest_snapshot == {}
    assert brief.secrets_refs == []


def test_root_manifest_chosen_over_nested(monkeypatch, org):
    """A nested astrolift.toml must not shadow the repo-root one."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("owner-repo-sha/astrolift.toml", b'[skills.root]\nsystem_prompt = "root"\n')
        zf.writestr(
            "owner-repo-sha/examples/astrolift.toml",
            b'[skills.nested]\nsystem_prompt = "nested"\n',
        )
    _patch_get(monkeypatch, _FakeResponse(buf.getvalue()))

    brief = brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/repo")

    assert brief.manifest_snapshot["skill_slug"] == "root"


def test_github_error_propagates(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(b"", status_ok=False))

    with pytest.raises(requests.HTTPError):
        brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/missing")

    assert Brief.objects.filter(organization=org).count() == 0


def test_pat_sent_as_bearer_when_configured(monkeypatch, org, settings):
    settings.GITHUB_PAT = "ghp_testtoken"
    capture: dict = {}
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)), capture=capture)

    brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/repo")

    assert capture["kwargs"]["headers"]["Authorization"] == "Bearer ghp_testtoken"
    assert capture["url"].endswith("/repos/owner/repo/zipball/main")


def test_no_auth_header_without_pat(monkeypatch, org, settings):
    settings.GITHUB_PAT = ""
    capture: dict = {}
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)), capture=capture)

    brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/repo")

    assert "Authorization" not in capture["kwargs"]["headers"]


# ---------------------------------------------------------------------------
# Blob storage path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SCM provider path (non-GitHub: e.g. GitLab) via a SourceConnection
# ---------------------------------------------------------------------------


class _FakeGitlabConn:
    """SourceConnection-shaped stand-in routed through the SCM provider."""

    kind = "gitlab_pat"


def test_source_connection_fetches_via_scm_provider(monkeypatch, org):
    """When a SourceConnection is supplied, the zipball comes from the
    SCM provider abstraction — not the hardcoded GitHub requests.get
    path — so a GitLab config repo assembles a valid Brief."""
    calls = {}

    def _fake_fetch_zipball(connection, *, repo_full_name, ref):
        calls["connection"] = connection
        calls["repo_full_name"] = repo_full_name
        calls["ref"] = ref
        return _make_zipball(_FULL_TOML)

    monkeypatch.setattr("astrolift_scm.providers.fetch_zipball", _fake_fetch_zipball)

    # Guard: the legacy GitHub path must NOT be touched on this branch.
    def _boom(*_a, **_k):
        raise AssertionError("requests.get must not be called on the SCM-provider path")

    monkeypatch.setattr(brief_assembler.requests, "get", _boom)

    conn = _FakeGitlabConn()
    brief = brief_assembler.assemble_agent_brief(
        organization=org,
        config_repo="acme-group/agent-config",
        config_branch="release",
        context={"task": "t-gl"},
        source_connection=conn,
    )

    assert brief.status == Brief.Status.READY
    assert brief.manifest_snapshot["skill_slug"] == "reviewer"
    # The provider got the repo/ref verbatim and the connection we passed.
    assert calls["connection"] is conn
    assert calls["repo_full_name"] == "acme-group/agent-config"
    assert calls["ref"] == "release"


def test_source_connection_provider_error_propagates(monkeypatch, org):
    """A provider-side failure surfaces to the caller and no Brief is
    written (the assembler never swallows the SCM error)."""
    from astrolift_scm.providers import ProviderError

    def _fake_fetch_zipball(connection, *, repo_full_name, ref):
        raise ProviderError("AUTH_FAILED", "token rejected", recoverable=True)

    monkeypatch.setattr("astrolift_scm.providers.fetch_zipball", _fake_fetch_zipball)

    with pytest.raises(ProviderError):
        brief_assembler.assemble_agent_brief(
            organization=org,
            config_repo="acme-group/agent-config",
            source_connection=_FakeGitlabConn(),
        )

    assert Brief.objects.filter(organization=org).count() == 0


def test_bundle_uploaded_when_blob_store_available(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_FULL_TOML)))

    uploads: list[dict] = []

    class _FakeDriver:
        def upload(self, key, data, *, content_type="application/octet-stream"):
            uploads.append({"key": key, "data": data, "content_type": content_type})

    monkeypatch.setattr(
        "astrolift_pipelines.artifact_store._get_blob_driver",
        lambda _org: _FakeDriver(),
    )

    brief = brief_assembler.assemble_agent_brief(organization=org, config_repo="owner/repo")

    assert len(uploads) == 1
    up = uploads[0]
    assert up["content_type"] == "application/zip"
    assert up["key"] == f"{org.slug}/payloads/{brief.content_hash}/bundle.zip"
    assert brief.storage_key == up["key"]
    # The exact zipball bytes are what get stored.
    assert up["data"] == _make_zipball(_FULL_TOML)


# ---------------------------------------------------------------------------
# manifest_path (monorepo: many agents per repo)
# ---------------------------------------------------------------------------

_AGENT_A_TOML = b"""
[skills.agent_a]
system_prompt = "I am agent A."
tools = ["t_a"]
"""

_AGENT_B_TOML = b"""
[skills.agent_b]
system_prompt = "I am agent B."
tools = ["t_b"]
"""


def _make_monorepo_zip(top_dir: str = "owner-repo-abc123") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top_dir}/astrolift.toml", b'[skills.lib]\nsystem_prompt = "root"\n')
        zf.writestr(f"{top_dir}/agents/agent-a/astrolift.toml", _AGENT_A_TOML)
        zf.writestr(f"{top_dir}/agents/agent-b/astrolift.toml", _AGENT_B_TOML)
    return buf.getvalue()


def test_select_manifest_member_rootmost_and_path():
    names = [
        "owner-repo-sha/astrolift.toml",
        "owner-repo-sha/agents/a/astrolift.toml",
        "owner-repo-sha/README.md",
    ]
    # No path -> root-most (historical behaviour).
    assert brief_assembler.select_manifest_member(names) == "owner-repo-sha/astrolift.toml"
    # File path and directory form both resolve the subfolder manifest.
    assert (brief_assembler.select_manifest_member(names, "agents/a/astrolift.toml")
            == "owner-repo-sha/agents/a/astrolift.toml")
    assert (brief_assembler.select_manifest_member(names, "agents/a")
            == "owner-repo-sha/agents/a/astrolift.toml")
    assert brief_assembler.select_manifest_member(names, "agents/missing") is None
    assert brief_assembler.select_manifest_member([]) is None


def test_manifest_path_selects_subfolder_agent(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_monorepo_zip()))
    brief = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo",
        manifest_path="agents/agent-a/astrolift.toml",
    )
    assert brief.manifest_snapshot["skill_slug"] == "agent_a"
    assert brief.manifest_snapshot["system_prompt"] == "I am agent A."


def test_manifest_path_directory_form(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_monorepo_zip()))
    brief = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo", manifest_path="agents/agent-b",
    )
    assert brief.manifest_snapshot["skill_slug"] == "agent_b"


def test_different_manifest_paths_are_distinct_briefs(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_monorepo_zip()))
    a = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo",
        manifest_path="agents/agent-a/astrolift.toml", context={"task": "t"},
    )
    b = brief_assembler.assemble_agent_brief(
        organization=org, config_repo="owner/repo",
        manifest_path="agents/agent-b/astrolift.toml", context={"task": "t"},
    )
    # Same repo+branch+context, different manifest_path -> distinct briefs.
    assert a.pk != b.pk
    assert a.content_hash != b.content_hash


def test_empty_manifest_path_hash_matches_pre_change_formula(org):
    # Backward-compat: omitting manifest_path must hash identically to the
    # historical {org, zip_sha256, context} payload (no dedup churn).
    import hashlib
    import json

    expected = hashlib.sha256(
        json.dumps(
            {"org": str(org.guid), "zip_sha256": "abc", "context": {"task": "t"}},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert brief_assembler._content_hash(
        organization=org, zip_digest="abc", context={"task": "t"},
    ) == expected


def test_missing_manifest_path_raises(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_monorepo_zip()))
    with pytest.raises(brief_assembler.ManifestNotFoundError):
        brief_assembler.assemble_agent_brief(
            organization=org, config_repo="owner/repo",
            manifest_path="agents/does-not-exist/astrolift.toml",
        )
