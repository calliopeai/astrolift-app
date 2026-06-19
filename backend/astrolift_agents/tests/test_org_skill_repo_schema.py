"""GraphQL resolver tests for per-org skill repos (spec 39d).

Mirrors ``test_skill_schema.py``: resolvers exercised by direct invocation
with a controllable permission resolver + a bound tenant context. The database
is real. Covers the RBAC gate (``scm.connect`` for writes, ``scm.read`` for the
list), tenancy isolation (a foreign org can't see / mutate another org's
repos), unique-alias-per-org, and the public/private connection link.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_agents.models import OrgSkillRepo
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    RegisterOrgSkillRepoInput,
    RemoveOrgSkillRepoInput,
    UpdateOrgSkillRepoInput,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
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
    return Organization.objects.create(name="Repos Acme", slug="repos-acme")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Repos Other", slug="repos-other")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _grant_write(resolver):
    # register/update/remove gate on scm.connect (mirroring source registration).
    resolver.grant(Permission.SCM_CONNECT)


def _grant_read(resolver):
    # orgSkillRepos gates on scm.read (mirroring the source connections list).
    resolver.grant(Permission.SCM_READ)


def _mk_connection(org, *, kind="github_pat", login="acme"):
    return SourceConnection.objects.create(
        organization=org,
        kind=kind,
        account_login=login,
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
    )


def _register(org, **overrides):
    payload = {
        "alias": "acme",
        "repo_full_name": "acme/dev-skills",
        "source_kind": "github",
        "default_ref": "main",
        "display_name": None,
        "source_connection_id": None,
    }
    payload.update(overrides)
    return AgentsMutation().register_org_skill_repo(
        SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
        input=RegisterOrgSkillRepoInput(**payload),
        org_id=str(org.guid),
    )


# ---------------------------------------------------------------------------
# register: happy path (public + private)
# ---------------------------------------------------------------------------


def test_register_public_repo(permission_resolver, org, with_tenant_org):
    _grant_write(permission_resolver)
    with with_tenant_org(org):
        result = _register(org)

    assert result.ok is True
    assert result.data.alias == "acme"
    assert result.data.repo_full_name == "acme/dev-skills"
    assert result.data.is_private is False
    assert result.data.source_connection_id is None
    row = OrgSkillRepo.objects.get(organization=org, alias="acme")
    assert row.default_ref == "main"
    assert row.source_connection_id is None


def test_register_private_repo_links_connection(permission_resolver, org, with_tenant_org):
    _grant_write(permission_resolver)
    conn = _mk_connection(org)
    with with_tenant_org(org):
        result = _register(org, source_connection_id=str(conn.guid))

    assert result.ok is True
    assert result.data.is_private is True
    assert str(result.data.source_connection_id) == str(conn.guid)
    assert OrgSkillRepo.objects.get(alias="acme").source_connection_id == conn.id


# ---------------------------------------------------------------------------
# register: validation + RBAC + tenancy
# ---------------------------------------------------------------------------


def test_register_requires_scm_connect(info, org, with_tenant_org):
    # No grant -> @mutation_audit converts PermissionDenied to an envelope.
    with with_tenant_org(org):
        result = _register(org)
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert OrgSkillRepo.objects.count() == 0


def test_register_duplicate_alias_rejected(permission_resolver, org, with_tenant_org):
    _grant_write(permission_resolver)
    with with_tenant_org(org):
        first = _register(org)
        assert first.ok is True
        dup = _register(org, repo_full_name="acme/other-skills")

    assert dup.ok is False
    assert dup.errors[0].code == "CONFLICT"
    assert dup.errors[0].field == "alias"
    assert OrgSkillRepo.objects.filter(organization=org, alias="acme").count() == 1


def test_register_same_alias_allowed_in_different_orgs(
    permission_resolver, org, other_org, with_tenant_org
):
    """Alias uniqueness is per-org: two orgs can each register 'acme'."""
    _grant_write(permission_resolver)
    with with_tenant_org(org):
        a = _register(org)
    with with_tenant_org(other_org):
        b = _register(other_org)

    assert a.ok is True and b.ok is True
    assert OrgSkillRepo.objects.filter(alias="acme").count() == 2


def test_register_blank_alias_rejected(permission_resolver, org, with_tenant_org):
    _grant_write(permission_resolver)
    with with_tenant_org(org):
        result = _register(org, alias="  ")
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "alias"


def test_register_alias_with_slash_rejected(permission_resolver, org, with_tenant_org):
    _grant_write(permission_resolver)
    with with_tenant_org(org):
        result = _register(org, alias="acme/sub")
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "alias"


def test_register_unknown_source_kind_rejected(permission_resolver, org, with_tenant_org):
    _grant_write(permission_resolver)
    with with_tenant_org(org):
        result = _register(org, source_kind="mercurial")
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "sourceKind"


def test_register_foreign_org_id_rejected(permission_resolver, org, other_org, with_tenant_org):
    """Passing another org's GUID while tenant-bound to ``org`` is rejected
    (the source can't be written into a foreign org)."""
    _grant_write(permission_resolver)
    with with_tenant_org(org):
        result = AgentsMutation().register_org_skill_repo(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            input=RegisterOrgSkillRepoInput(
                alias="acme",
                repo_full_name="acme/dev-skills",
                source_kind="github",
                default_ref="main",
                display_name=None,
                source_connection_id=None,
            ),
            org_id=str(other_org.guid),
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert OrgSkillRepo.objects.filter(organization=other_org).count() == 0


def test_register_foreign_connection_rejected(permission_resolver, org, other_org, with_tenant_org):
    """A connection owned by another org can't be linked (cross-tenant
    credential reuse must be impossible)."""
    _grant_write(permission_resolver)
    foreign_conn = _mk_connection(other_org)
    with with_tenant_org(org):
        result = _register(org, source_connection_id=str(foreign_conn.guid))
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "sourceConnectionId"
    assert OrgSkillRepo.objects.count() == 0


# ---------------------------------------------------------------------------
# orgSkillRepos query: listing + scoping + RBAC
# ---------------------------------------------------------------------------


def test_list_returns_org_repos_ordered(permission_resolver, info, org, with_tenant_org):
    _grant_read(permission_resolver)
    OrgSkillRepo.objects.create(organization=org, alias="zeta", repo_full_name="o/z")
    OrgSkillRepo.objects.create(organization=org, alias="alpha", repo_full_name="o/a")

    with with_tenant_org(org):
        rows = AgentsQuery().org_skill_repos(info(), org_id=str(org.guid))

    assert [r.alias for r in rows] == ["alpha", "zeta"]


def test_list_excludes_other_org_repos(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_read(permission_resolver)
    OrgSkillRepo.objects.create(organization=org, alias="mine", repo_full_name="o/m")
    OrgSkillRepo.objects.create(organization=other_org, alias="theirs", repo_full_name="o/t")

    with with_tenant_org(org):
        rows = AgentsQuery().org_skill_repos(info(), org_id=str(org.guid))

    assert {r.alias for r in rows} == {"mine"}


def test_list_excludes_soft_deleted(permission_resolver, info, org, with_tenant_org):
    _grant_read(permission_resolver)
    live = OrgSkillRepo.objects.create(organization=org, alias="live", repo_full_name="o/l")
    gone = OrgSkillRepo.objects.create(organization=org, alias="gone", repo_full_name="o/g")
    gone.soft_delete()

    with with_tenant_org(org):
        rows = AgentsQuery().org_skill_repos(info(), org_id=str(org.guid))

    assert {r.alias for r in rows} == {"live"}
    assert live.alias == "live"


def test_list_requires_scm_read(info, org, with_tenant_org):
    from graphql import GraphQLError

    with with_tenant_org(org):
        with pytest.raises((GraphQLError, Exception)):
            AgentsQuery().org_skill_repos(info(), org_id=str(org.guid))


# ---------------------------------------------------------------------------
# update: partial fields, connection attach/detach, tenancy
# ---------------------------------------------------------------------------


def test_update_partial_fields(permission_resolver, info, org, with_tenant_org):
    _grant_write(permission_resolver)
    repo = OrgSkillRepo.objects.create(organization=org, alias="acme", repo_full_name="acme/old")

    with with_tenant_org(org):
        result = AgentsMutation().update_org_skill_repo(
            info(),
            input=UpdateOrgSkillRepoInput(
                id=str(repo.guid),
                repo_full_name="acme/new",
                default_ref="develop",
                display_name="Acme Dev Skills",
            ),
        )

    assert result.ok is True
    repo.refresh_from_db()
    assert repo.repo_full_name == "acme/new"
    assert repo.default_ref == "develop"
    assert repo.display_name == "Acme Dev Skills"
    # Untouched field unchanged.
    assert repo.alias == "acme"


def test_update_attaches_connection(permission_resolver, info, org, with_tenant_org):
    _grant_write(permission_resolver)
    repo = OrgSkillRepo.objects.create(organization=org, alias="acme", repo_full_name="acme/dev")
    conn = _mk_connection(org)

    with with_tenant_org(org):
        result = AgentsMutation().update_org_skill_repo(
            info(),
            input=UpdateOrgSkillRepoInput(id=str(repo.guid), source_connection_id=str(conn.guid)),
        )

    assert result.ok is True
    assert result.data.is_private is True
    repo.refresh_from_db()
    assert repo.source_connection_id == conn.id


def test_update_detaches_connection(permission_resolver, info, org, with_tenant_org):
    _grant_write(permission_resolver)
    conn = _mk_connection(org)
    repo = OrgSkillRepo.objects.create(
        organization=org, alias="acme", repo_full_name="acme/dev", source_connection=conn
    )

    with with_tenant_org(org):
        result = AgentsMutation().update_org_skill_repo(
            info(),
            input=UpdateOrgSkillRepoInput(id=str(repo.guid), detach_source_connection=True),
        )

    assert result.ok is True
    assert result.data.is_private is False
    repo.refresh_from_db()
    assert repo.source_connection_id is None


def test_update_attach_and_detach_both_rejected(permission_resolver, info, org, with_tenant_org):
    _grant_write(permission_resolver)
    conn = _mk_connection(org)
    repo = OrgSkillRepo.objects.create(organization=org, alias="acme", repo_full_name="acme/dev")

    with with_tenant_org(org):
        result = AgentsMutation().update_org_skill_repo(
            info(),
            input=UpdateOrgSkillRepoInput(
                id=str(repo.guid),
                source_connection_id=str(conn.guid),
                detach_source_connection=True,
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"


def test_update_foreign_org_repo_not_found(permission_resolver, info, org, other_org, with_tenant_org):
    """A repo in another org is NOT_FOUND for the caller (no cross-tenant edit)."""
    _grant_write(permission_resolver)
    foreign = OrgSkillRepo.objects.create(organization=other_org, alias="theirs", repo_full_name="o/t")

    with with_tenant_org(org):
        result = AgentsMutation().update_org_skill_repo(
            info(), input=UpdateOrgSkillRepoInput(id=str(foreign.guid), default_ref="x")
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    foreign.refresh_from_db()
    assert foreign.default_ref == "main"


def test_update_requires_scm_connect(info, org, with_tenant_org):
    repo = OrgSkillRepo.objects.create(organization=org, alias="acme", repo_full_name="acme/dev")
    with with_tenant_org(org):
        result = AgentsMutation().update_org_skill_repo(
            info(), input=UpdateOrgSkillRepoInput(id=str(repo.guid), default_ref="x")
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# remove: soft delete + tenancy
# ---------------------------------------------------------------------------


def test_remove_soft_deletes(permission_resolver, info, org, with_tenant_org):
    _grant_write(permission_resolver)
    repo = OrgSkillRepo.objects.create(organization=org, alias="acme", repo_full_name="acme/dev")

    with with_tenant_org(org):
        result = AgentsMutation().remove_org_skill_repo(
            info(), input=RemoveOrgSkillRepoInput(id=str(repo.guid))
        )

    assert result.ok is True
    # Soft-deleted: gone from the default manager, present in all_objects.
    assert not OrgSkillRepo.objects.filter(pk=repo.pk).exists()
    assert OrgSkillRepo.all_objects.get(pk=repo.pk).deleted_at is not None


def test_remove_allows_alias_reclaim(permission_resolver, info, org, with_tenant_org):
    """The partial-unique constraint scopes to live rows, so the alias frees up
    after a soft-delete."""
    _grant_write(permission_resolver)
    first = OrgSkillRepo.objects.create(organization=org, alias="acme", repo_full_name="acme/dev")

    with with_tenant_org(org):
        AgentsMutation().remove_org_skill_repo(info(), input=RemoveOrgSkillRepoInput(id=str(first.guid)))
        again = _register(org, repo_full_name="acme/dev-skills-v2")

    assert again.ok is True
    assert OrgSkillRepo.objects.filter(organization=org, alias="acme").count() == 1


def test_remove_foreign_org_repo_not_found(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_write(permission_resolver)
    foreign = OrgSkillRepo.objects.create(organization=other_org, alias="theirs", repo_full_name="o/t")

    with with_tenant_org(org):
        result = AgentsMutation().remove_org_skill_repo(
            info(), input=RemoveOrgSkillRepoInput(id=str(foreign.guid))
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    foreign.refresh_from_db()
    assert foreign.deleted_at is None


def test_remove_requires_scm_connect(info, org, with_tenant_org):
    repo = OrgSkillRepo.objects.create(organization=org, alias="acme", repo_full_name="acme/dev")
    with with_tenant_org(org):
        result = AgentsMutation().remove_org_skill_repo(
            info(), input=RemoveOrgSkillRepoInput(id=str(repo.guid))
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
