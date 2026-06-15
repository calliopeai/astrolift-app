"""GraphQL resolver + seed-command tests for the Skill/ToolDef registry (#42).

Resolvers are exercised by direct invocation (matching the forms/billing
test convention) with a controllable permission resolver + tenant
context bound. The network boundary is mocked; the database is real.
"""

from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import pytest
import requests

from astrolift_agents.models import Skill, ToolDef
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services import brief_assembler
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

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
def info():
    def _make(request=None):
        return SimpleNamespace(context=SimpleNamespace(user=None, request=request))

    return _make


@pytest.fixture
def org():
    return Organization.objects.create(name="Agents Acme", slug="agents-acme")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Agents Other", slug="agents-other")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _grant_all(resolver):
    # The live resolvers gate the skill read surface on APP_READ and the
    # repo importer on SKILL_IMPORT (astrolift_agents/schema/*.py).
    for p in (Permission.APP_READ, Permission.SKILL_IMPORT):
        resolver.grant(p)


def _mk_skill(org, slug, *, agent_type="", tags=None, is_global=False, organization_set=True):
    return Skill.objects.create(
        organization=org if organization_set else None,
        name=slug.title(),
        slug=slug,
        content=f"content for {slug}",
        agent_type=agent_type,
        scaffolding_tags=tags or [],
        is_global=is_global,
    )


# ---------------------------------------------------------------------------
# skills() query: scoping
# ---------------------------------------------------------------------------


def test_skills_lists_org_and_global(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_all(permission_resolver)
    _mk_skill(org, "my-skill")
    _mk_skill(None, "global-skill", is_global=True, organization_set=False)
    _mk_skill(other_org, "their-skill")

    with with_tenant_org(org):
        rows = AgentsQuery().skills(info(), org_id=str(org.guid))

    slugs = {r.slug for r in rows}
    assert "my-skill" in slugs
    assert "global-skill" in slugs
    # Another org's private skill must not leak.
    assert "their-skill" not in slugs


def test_skills_global_only_narrows_to_global(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    _mk_skill(org, "my-skill")
    _mk_skill(None, "global-skill", is_global=True, organization_set=False)

    with with_tenant_org(org):
        rows = AgentsQuery().skills(info(), org_id=str(org.guid), is_global=True)

    slugs = {r.slug for r in rows}
    assert slugs == {"global-skill"}


def test_skill_detail_returns_org_skill(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    org_row = _mk_skill(org, "my-skill")

    with with_tenant_org(org):
        result = AgentsQuery().skill(info(), id=str(org_row.guid))

    assert result is not None
    assert result.is_global is False
    assert str(result.id) == str(org_row.guid)


def test_skill_detail_resolves_global(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    glob = _mk_skill(None, "shared", is_global=True, organization_set=False)

    with with_tenant_org(org):
        result = AgentsQuery().skill(info(), id=str(glob.guid))

    assert result is not None
    assert result.is_global is True
    assert str(result.id) == str(glob.guid)


def test_skill_detail_cross_tenant_returns_null(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_all(permission_resolver)
    secret = _mk_skill(org, "secret-skill")

    with with_tenant_org(other_org):
        result = AgentsQuery().skill(info(), id=str(secret.guid))

    assert result is None


def test_tool_defs_for_skill(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    skill = _mk_skill(org, "with-tools")
    ToolDef.objects.create(skill=skill, name="cat", slug="cat", adapter="python_fn", handler_ref="tools.cat")

    with with_tenant_org(org):
        rows = AgentsQuery().tool_defs(info(), skill_id=str(skill.guid))

    assert [t.slug for t in rows] == ["cat"]
    assert rows[0].adapter == "python_fn"


# ---------------------------------------------------------------------------
# skills() query: permission gate
# ---------------------------------------------------------------------------


def test_skills_requires_app_read(info, org, with_tenant_org):
    with with_tenant_org(org):
        with pytest.raises(PermissionDenied) as exc_info:
            AgentsQuery().skills(info(), org_id=str(org.guid))
    assert exc_info.value.permission.value == "app.read"


# ---------------------------------------------------------------------------
# importSkillsFromRepo mutation
# ---------------------------------------------------------------------------

_TOML = b"""
[skills.reviewer]
content = "You review pull requests."
agent_type = "claude"
scaffolding_tags = ["code-review"]

[tools.read_file]
skill = "reviewer"
commands = ["cat"]
capability_group = "dev"
"""


def _zipball(toml_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("owner-repo-sha/astrolift.toml", toml_bytes)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes, *, status_ok: bool = True):
        self.content = content
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError("404 Not Found")


def _patch_get(monkeypatch, response: _FakeResponse):
    monkeypatch.setattr(brief_assembler.requests, "get", lambda url, **kw: response)


def test_import_mutation_succeeds(permission_resolver, info, org, with_tenant_org, monkeypatch):
    _grant_all(permission_resolver)
    _patch_get(monkeypatch, _FakeResponse(_zipball(_TOML)))

    with with_tenant_org(org):
        result = AgentsMutation().import_skills_from_repo(
            info(),
            repo_url="https://github.com/acme/agent-config",
            branch="main",
        )

    assert result.ok is True
    assert result.data.imported_skills == ["reviewer"]
    assert result.data.imported_tools == ["read_file"]
    assert result.data.source_ref == "acme/agent-config@main"
    assert Skill.objects.filter(organization=org, slug="reviewer").exists()


def test_import_mutation_defaults_branch_to_main(
    permission_resolver, info, org, with_tenant_org, monkeypatch
):
    _grant_all(permission_resolver)
    captured: dict = {}

    def _capture_get(url, **kw):
        captured["url"] = url
        return _FakeResponse(_zipball(_TOML))

    monkeypatch.setattr(brief_assembler.requests, "get", _capture_get)

    with with_tenant_org(org):
        result = AgentsMutation().import_skills_from_repo(
            info(), repo_url="https://github.com/acme/agent-config"
        )

    assert result.ok is True
    assert captured["url"].endswith("/repos/acme/agent-config/zipball/main")


def test_import_mutation_rejects_non_github_url(permission_resolver, info, org, with_tenant_org, monkeypatch):
    _grant_all(permission_resolver)
    # Fetch must never run for an invalid URL.
    monkeypatch.setattr(
        brief_assembler.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    with with_tenant_org(org):
        result = AgentsMutation().import_skills_from_repo(
            info(), repo_url="https://gitlab.com/acme/agent-config"
        )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "repoUrl"
    assert Skill.objects.filter(organization=org).count() == 0


def test_import_mutation_surfaces_github_error_as_precondition(
    permission_resolver, info, org, with_tenant_org, monkeypatch
):
    _grant_all(permission_resolver)
    _patch_get(monkeypatch, _FakeResponse(b"", status_ok=False))

    with with_tenant_org(org):
        result = AgentsMutation().import_skills_from_repo(info(), repo_url="https://github.com/acme/missing")

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"
    assert Skill.objects.filter(organization=org).count() == 0


def test_import_mutation_requires_skill_import(info, org, with_tenant_org):
    # No grants -> @mutation_audit converts PermissionDenied to an envelope.
    with with_tenant_org(org):
        result = AgentsMutation().import_skills_from_repo(
            info(), repo_url="https://github.com/acme/agent-config"
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# seed_builtin_tools command
# ---------------------------------------------------------------------------


def test_seed_builtin_tools_creates_global_catalog():
    from django.core.management import call_command

    call_command("seed_builtin_tools")

    host = Skill.objects.get(organization__isnull=True, slug="builtin-tools")
    assert host.is_global is True

    tools = {t.slug: t for t in ToolDef.objects.filter(skill=host)}
    assert set(tools) == {
        "dev-toolkit",
        "cloud-aws",
        "k8s-toolkit",
        "data-clients",
        "infra-terraform",
    }
    dev = tools["dev-toolkit"]
    assert dev.capability_group == "dev"
    assert dev.commands == ["git", "gh", "make", "curl", "jq", "yq", "ripgrep"]
    assert dev.is_builtin is True
    assert dev.adapter == ToolDef.Adapter.PYTHON_FN
    assert tools["infra-terraform"].commands == ["terraform"]


def test_seed_builtin_tools_is_idempotent():
    from django.core.management import call_command

    call_command("seed_builtin_tools")
    call_command("seed_builtin_tools")

    assert Skill.objects.filter(slug="builtin-tools").count() == 1
    assert ToolDef.objects.filter(skill__slug="builtin-tools").count() == 5


# ---------------------------------------------------------------------------
# seed_global_skills command
# ---------------------------------------------------------------------------


def test_seed_global_skills_creates_personas():
    from django.core.management import call_command

    call_command("seed_global_skills")

    skills = {s.slug: s for s in Skill.objects.filter(is_global=True, organization__isnull=True)}
    expected = {
        "code-review-fix",
        "infra-audit",
        "data-pipeline-qa",
        "pr-description-writer",
        "workflow-supervisor",
    }
    assert expected <= set(skills)

    cr = skills["code-review-fix"]
    assert cr.agent_type == "claude"
    assert cr.scaffolding_tags == ["code-review"]
    assert "code reviewer" in cr.content
    assert len(cr.content_hash) == 64
    assert skills["data-pipeline-qa"].agent_type == "codex"


def test_seed_global_skills_is_idempotent_and_stable_version():
    from django.core.management import call_command

    call_command("seed_global_skills")
    first = Skill.objects.get(slug="code-review-fix")
    assert first.skill_version == 1

    call_command("seed_global_skills")
    again = Skill.objects.get(slug="code-review-fix")
    # No duplicate + unchanged content keeps the version pinned.
    assert Skill.objects.filter(slug="code-review-fix").count() == 1
    assert again.skill_version == 1
