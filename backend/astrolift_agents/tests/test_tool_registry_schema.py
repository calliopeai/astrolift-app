"""Schema tests for the Tool Registry surface (#875).

Covers:
- orgToolDefs query: returns tool defs across all skills visible to org
- importSkillsFromRepo mutation: wired resolver delegates to the service
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
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


# ─── Fixtures ────────────────────────────────────────────────────────────────


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
    return Organization.objects.create(name="Tool Registry Org", slug="tool-registry-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other Org", slug="other-org-tr")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _grant_all(resolver):
    for p in (Permission.SKILL_READ, Permission.SKILL_WRITE, Permission.SKILL_IMPORT):
        resolver.grant(p)


def _mk_skill_with_tool(org, skill_slug, tool_slug, *, adapter="python_fn"):
    skill = Skill.objects.create(
        organization=org,
        name=skill_slug.title(),
        slug=skill_slug,
        content="",
    )
    tool = ToolDef.objects.create(
        skill=skill,
        name=tool_slug.title(),
        slug=tool_slug,
        adapter=adapter,
        handler_ref="",
    )
    return skill, tool


# ─── orgToolDefs query ───────────────────────────────────────────────────────


def test_org_tool_defs_returns_own_tools(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    _mk_skill_with_tool(org, "my-skill", "my-tool")

    with with_tenant_org(org):
        rows = AgentsQuery().org_tool_defs(info(), org_id=str(org.guid))

    assert len(rows) == 1
    assert rows[0].slug == "my-tool"


def test_org_tool_defs_excludes_other_org_tools(
    permission_resolver, info, org, other_org, with_tenant_org
):
    _grant_all(permission_resolver)
    _mk_skill_with_tool(org, "my-skill", "my-tool")
    _mk_skill_with_tool(other_org, "their-skill", "their-tool")

    with with_tenant_org(org):
        rows = AgentsQuery().org_tool_defs(info(), org_id=str(org.guid))

    slugs = {r.slug for r in rows}
    assert "my-tool" in slugs
    assert "their-tool" not in slugs


def test_org_tool_defs_includes_global_skill_tools(
    permission_resolver, info, org, with_tenant_org
):
    _grant_all(permission_resolver)
    global_skill = Skill.objects.create(
        organization=None,
        name="Global Skill",
        slug="global-skill",
        content="",
        is_global=True,
    )
    ToolDef.objects.create(
        skill=global_skill,
        name="Global Tool",
        slug="global-tool",
        adapter="python_fn",
        handler_ref="",
    )
    _mk_skill_with_tool(org, "own-skill", "own-tool")

    with with_tenant_org(org):
        rows = AgentsQuery().org_tool_defs(info(), org_id=str(org.guid))

    slugs = {r.slug for r in rows}
    assert "global-tool" in slugs
    assert "own-tool" in slugs


def test_org_tool_defs_requires_permission(permission_resolver, info, org, with_tenant_org):
    # No grants.
    with pytest.raises(Exception):
        with with_tenant_org(org):
            AgentsQuery().org_tool_defs(info(), org_id=str(org.guid))


# ─── importSkillsFromRepo mutation ───────────────────────────────────────────


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


def test_import_skills_from_repo_wires_through(
    permission_resolver, info, org, with_tenant_org, monkeypatch
):
    """The importSkillsFromRepo resolver delegates to the service and
    returns an ImportSkillsResult payload."""
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


def test_import_skills_from_repo_rejects_non_github(
    permission_resolver, info, org, with_tenant_org
):
    _grant_all(permission_resolver)

    with with_tenant_org(org):
        result = AgentsMutation().import_skills_from_repo(
            info(),
            repo_url="https://gitlab.com/acme/cfg",
            branch="main",
        )

    assert result.ok is False
    assert any("github.com" in e.message for e in result.errors)


def test_import_skills_from_repo_requires_permission(
    permission_resolver, info, org, with_tenant_org
):
    # No grants — resolver must reject before hitting the service.
    with pytest.raises(Exception):
        with with_tenant_org(org):
            AgentsMutation().import_skills_from_repo(
                info(),
                repo_url="https://github.com/acme/agent-config",
            )
