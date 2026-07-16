"""Tests for the GitHub -> Skill/ToolDef import service (#42).

Runs against real Postgres (pytest-django) and exercises the real
zip/TOML parse + upsert path against an in-memory zipball. Only the
network boundary (``requests.get``) is mocked — never the database.
"""

from __future__ import annotations

import io
import zipfile

import pytest
import requests

from astrolift_agents.models import Skill, ToolDef
from astrolift_agents.services import brief_assembler, skill_importer
from astrolift_agents.services.skill_importer import (
    InvalidRepoURLError,
    import_skills_from_repo,
    parse_repo_url,
)
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Import Org", slug="skill-import-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other Org", slug="skill-import-other")


# ---------------------------------------------------------------------------
# Zipball helpers
# ---------------------------------------------------------------------------

_TOML = b"""
[skills.reviewer]
name = "Reviewer"
description = "Reviews PRs"
content = "You review pull requests."
agent_type = "claude"
scaffolding_tags = ["code-review"]
dependencies = ["ruff", "mypy"]

[skills.builder]
system_prompt = "You build things."
agent_type = "codex"

[tools.read_file]
skill = "reviewer"
description = "Read a file"
commands = ["cat"]
capability_group = "dev"
agent_type_bindings = ["claude"]

[tools.run_tests]
skill = "builder"
adapter = "http_endpoint"
handler_ref = "https://ci.example/run"
required_packages = [{ manager = "apt", name = "make" }]
"""


def _make_zipball(toml_bytes: bytes | None, *, top_dir: str = "owner-repo-abc123") -> bytes:
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

    # _fetch_zipball lives in brief_assembler and is reused by the importer.
    monkeypatch.setattr(brief_assembler.requests, "get", _fake_get)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets/tree/main", "acme/widgets"),
        ("http://github.com/acme/widgets", "acme/widgets"),
        ("https://www.github.com/acme/widgets", "acme/widgets"),
        ("github.com/acme/widgets", "acme/widgets"),
    ],
)
def test_parse_repo_url_accepts_github(url, expected):
    assert parse_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://gitlab.com/acme/widgets",
        "https://example.com/acme/widgets",
        "https://github.com/acme",
        "https://github.com/",
        "git@github.com:acme/widgets.git",  # ssh form, not a parseable https host
    ],
)
def test_parse_repo_url_rejects_non_github(url):
    with pytest.raises(InvalidRepoURLError):
        parse_repo_url(url)


# ---------------------------------------------------------------------------
# Import: happy path
# ---------------------------------------------------------------------------


def test_import_creates_skills_and_tools(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_TOML)))

    result = import_skills_from_repo(
        organization=org,
        repo_url="https://github.com/acme/agent-config",
        branch="main",
    )

    assert sorted(result.imported_skills) == ["builder", "reviewer"]
    assert sorted(result.imported_tools) == ["read_file", "run_tests"]
    assert result.source_ref == "acme/agent-config@main"

    reviewer = Skill.objects.get(organization=org, slug="reviewer")
    assert reviewer.name == "Reviewer"
    assert reviewer.content == "You review pull requests."
    assert reviewer.agent_type == "claude"
    assert reviewer.scaffolding_tags == ["code-review"]
    assert reviewer.dependencies == ["ruff", "mypy"]
    assert reviewer.is_global is False
    assert reviewer.skill_version == 1
    assert len(reviewer.content_hash) == 64

    # The builder skill picked up the system_prompt alias as content.
    builder = Skill.objects.get(organization=org, slug="builder")
    assert builder.content == "You build things."
    assert builder.agent_type == "codex"

    read_file = ToolDef.objects.get(skill=reviewer, slug="read_file")
    assert read_file.commands == ["cat"]
    assert read_file.capability_group == "dev"
    assert read_file.agent_type_bindings == ["claude"]
    assert read_file.adapter == ToolDef.Adapter.PYTHON_FN  # default

    run_tests = ToolDef.objects.get(skill=builder, slug="run_tests")
    assert run_tests.adapter == ToolDef.Adapter.HTTP_ENDPOINT
    assert run_tests.handler_ref == "https://ci.example/run"
    assert run_tests.required_packages == [{"manager": "apt", "name": "make"}]


def test_import_scopes_rows_to_calling_org(monkeypatch, org, other_org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_TOML)))
    import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")

    # Nothing leaked into the other org.
    assert Skill.objects.filter(organization=other_org).count() == 0
    assert Skill.objects.filter(organization=org).count() == 2


# ---------------------------------------------------------------------------
# Import: idempotency + version bump
# ---------------------------------------------------------------------------


def test_reimport_is_idempotent_and_does_not_bump_unchanged_version(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_TOML)))
    import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")
    import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")

    # No duplicate rows.
    assert Skill.objects.filter(organization=org).count() == 2
    assert ToolDef.objects.filter(skill__organization=org).count() == 2

    reviewer = Skill.objects.get(organization=org, slug="reviewer")
    # Content unchanged across the two imports -> version stays at 1.
    assert reviewer.skill_version == 1


def test_reimport_bumps_version_when_content_changes(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(_TOML)))
    import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")

    changed = _TOML.replace(b"You review pull requests.", b"You review pull requests carefully.")
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(changed)))
    import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")

    reviewer = Skill.objects.get(organization=org, slug="reviewer")
    assert reviewer.skill_version == 2
    assert reviewer.content == "You review pull requests carefully."


# ---------------------------------------------------------------------------
# Import: edge cases
# ---------------------------------------------------------------------------


def test_single_skill_tool_binding_is_optional(monkeypatch, org):
    toml = b"""
[skills.solo]
content = "Only skill."

[tools.helper]
description = "no explicit skill ref"
commands = ["ls"]
"""
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(toml)))
    result = import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")

    assert result.imported_skills == ["solo"]
    assert result.imported_tools == ["helper"]
    solo = Skill.objects.get(organization=org, slug="solo")
    assert ToolDef.objects.get(skill=solo, slug="helper").commands == ["ls"]


def test_tool_with_unresolvable_skill_is_skipped(monkeypatch, org):
    toml = b"""
[skills.a]
content = "a"

[skills.b]
content = "b"

[tools.orphan]
skill = "does-not-exist"
commands = ["true"]
"""
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(toml)))
    result = import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")

    # Two skills imported; the orphan tool (bad ref, >1 skill so no
    # implicit binding) is skipped rather than failing the import.
    assert sorted(result.imported_skills) == ["a", "b"]
    assert result.imported_tools == []
    assert ToolDef.objects.filter(skill__organization=org).count() == 0


def test_repo_without_manifest_imports_nothing(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_zipball(None)))
    result = import_skills_from_repo(organization=org, repo_url="https://github.com/acme/cfg")

    assert result.imported_skills == []
    assert result.imported_tools == []
    assert Skill.objects.filter(organization=org).count() == 0


def test_github_http_error_propagates_and_creates_nothing(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(b"", status_ok=False))
    with pytest.raises(requests.HTTPError):
        import_skills_from_repo(organization=org, repo_url="https://github.com/acme/missing")
    assert Skill.objects.filter(organization=org).count() == 0


def test_invalid_url_raises_before_any_fetch(monkeypatch, org):
    called = {"n": 0}

    def _should_not_be_called(*a, **k):
        called["n"] += 1
        raise AssertionError("fetch must not run on an invalid URL")

    monkeypatch.setattr(skill_importer, "_fetch_zipball", _should_not_be_called)

    with pytest.raises(InvalidRepoURLError):
        import_skills_from_repo(organization=org, repo_url="https://gitlab.com/acme/cfg")
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# manifest_path: import a library that isn't at the repo root
# ---------------------------------------------------------------------------


def _make_subdir_zipball(toml_bytes: bytes, *, top_dir: str = "owner-repo-abc123") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top_dir}/astrolift.toml", b'[skills.root]\ncontent = "root lib"\n')
        zf.writestr(f"{top_dir}/libs/emr/astrolift.toml", toml_bytes)
    return buf.getvalue()


def test_import_uses_manifest_path_when_given(monkeypatch, org):
    _patch_get(monkeypatch, _FakeResponse(_make_subdir_zipball(_TOML)))
    result = import_skills_from_repo(
        organization=org,
        repo_url="https://github.com/acme/cfg",
        manifest_path="libs/emr/astrolift.toml",
    )
    # Pulled the subdir library (reviewer/builder), not the root one.
    assert set(result.imported_skills) == {"reviewer", "builder"}
    assert result.source_ref == "acme/cfg@main:libs/emr/astrolift.toml"
