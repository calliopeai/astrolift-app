"""createSourceRepository and the scm:read / scm:write token scopes (#2605)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from astrolift_identity.api_tokens import (
    ALLOWED_SCOPES,
    SCOPE_CATALOG,
    permissions_for_scopes,
    reset_current_api_token,
    set_current_api_token,
    token_scope_reaches,
)
from astrolift_identity.models import Organization, Role, RoleBinding
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError, repo_writes
from astrolift_scm.providers.repo_writes import RepoState
from astrolift_scm.schema import mutations as mut
from astrolift_scm.schema.queries import ScmQuery
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


def _connection(org, login, *, user=None, kind="github_pat", active=True):
    secret = encrypt_at_rest(b"test-secret")
    return SourceConnection.objects.create(
        organization=org,
        user=user,
        kind=kind,
        account_login=login,
        is_active=active,
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
    )


class FakeHost:
    def __init__(self):
        self.repos: dict[str, RepoState] = {}
        self.calls: list[tuple] = []
        self.create_error: ProviderError | None = None

    def state(self, connection, repo_full_name):
        self.calls.append(("state", connection.pk, repo_full_name))
        return self.repos.get(repo_full_name, RepoState(exists=False))

    def create(self, connection, owner, name, visibility, *, description="", auto_init=None):
        self.calls.append(("create", connection.pk, owner, name, visibility, description, auto_init))
        if self.create_error is not None:
            raise self.create_error
        state = RepoState(exists=True, empty=True, default_branch="main", writable=True)
        self.repos[f"{owner}/{name}"] = state
        return state


@pytest.fixture
def world(monkeypatch):
    w = ScopeWorld("scm-2605")
    w.user, w.other_user = make_user("scm-2605"), make_user("scm-other-2605")
    w.other_org = Organization.objects.create(name="Foreign", slug="foreign-scm-2605")
    w.connection = _connection(w.org, "acme")
    w.other_personal = _connection(w.org, "theirs", user=w.other_user, kind="github_oauth_user")
    w.foreign = _connection(w.other_org, "foreign")
    w.host = FakeHost()
    monkeypatch.setattr(repo_writes, "repository_state", w.host.state)
    monkeypatch.setattr(repo_writes, "create_repository", w.host.create)
    w.audits = []
    monkeypatch.setattr("core.mutations.emit_audit", w.audits.append)
    monkeypatch.setattr("astrolift_scm.schema.queries.list_repos", lambda *a, **k: [])
    return w


def _grant(w, *permissions, kind="ORG"):
    scope_id = {"ORG": w.org.pk, "TEAM": w.medops.pk}[kind]
    return bind_role(
        w.user,
        permissions=permissions or [Permission.SCM_WRITE],
        kind=kind,
        scope_id=scope_id,
        slug=uuid.uuid4().hex,
    )


@contextmanager
def _tenant(w):
    with tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk)):
        yield


@contextmanager
def _bearer(w, scopes, *, foreign=False, team=False):
    token = set_current_api_token(
        SimpleNamespace(
            organization_id=w.other_org.pk if foreign else w.org.pk,
            user_id=w.user.pk,
            team_id=w.medops.pk if team else None,
            scopes=scopes,
        )
    )
    try:
        yield
    finally:
        reset_current_api_token(token)


def _create(w, connection=None, **overrides):
    fields = {
        "connection_id": (connection or w.connection).guid,
        "owner": "acme",
        "name": "studio-app",
        "visibility": "private",
        **overrides,
    }
    return mut.ScmMutation().create_source_repository(
        make_info(w.user), input=mut.CreateSourceRepositoryInput(**fields)
    )


def _code(result):
    assert not result.ok, result
    return result.errors[0].code


# --- the mutation ----------------------------------------------------------


def test_creates_the_repository_and_audits_it(world):
    _grant(world)
    with _tenant(world):
        result = _create(world, description="  intake  ", auto_init=False)
    assert result.ok, result
    assert result.data.repo_full_name == "acme/studio-app"
    assert result.data.url == "https://github.com/acme/studio-app"
    assert result.data.default_branch == "main"
    assert result.data.created is True
    assert (
        "create",
        world.connection.pk,
        "acme",
        "studio-app",
        "private",
        "intake",
        False,
    ) in world.host.calls
    (entry,) = world.audits
    assert entry.action == "scm.repository.create" and entry.decision == "ALLOW"
    assert entry.target_kind == "SourceConnection" and entry.target_id == str(world.connection.guid)
    assert entry.extra == {"repo_full_name": "acme/studio-app", "outcome": "created"}
    assert entry.permissions == ("scm.write",)


def test_existing_repository_the_connection_can_push_to_is_returned(world):
    _grant(world)
    world.host.repos["acme/studio-app"] = RepoState(
        exists=True, empty=False, default_branch="trunk", commit_count=2, writable=True
    )
    with _tenant(world):
        first = _create(world)
        second = _create(world)
    for result in (first, second):
        assert result.ok and result.data.created is False and result.data.default_branch == "trunk"
    assert not [c for c in world.host.calls if c[0] == "create"]
    assert world.audits[-1].extra["outcome"] == "existing"


def test_retry_after_a_create_returns_it(world):
    _grant(world)
    with _tenant(world):
        assert _create(world).data.created is True
        assert _create(world).data.created is False
    assert len([c for c in world.host.calls if c[0] == "create"]) == 1


def test_existing_repository_the_connection_cannot_push_to_is_name_taken(world):
    _grant(world)
    world.host.repos["acme/studio-app"] = RepoState(exists=True, empty=False, writable=False)
    with _tenant(world):
        assert _code(_create(world)) == "NAME_TAKEN"
    assert not [c for c in world.host.calls if c[0] == "create"]


@pytest.mark.parametrize(
    "provider_code,code",
    [
        ("CONFLICT", "NAME_TAKEN"),
        ("NOT_FOUND", "OWNER_NOT_ALLOWED"),
        ("AUTH_FAILED", "FORBIDDEN"),
        ("PRECONDITION", "FORBIDDEN"),
        ("VALIDATION", "VALIDATION"),
    ],
)
def test_host_refusals_map_to_safe_codes_without_the_host_body(world, provider_code, code):
    _grant(world)
    world.host.create_error = ProviderError(provider_code, "host said: secret-ish body", recoverable=True)
    with _tenant(world):
        result = _create(world)
    assert _code(result) == code
    assert "secret-ish" not in result.errors[0].message


def test_inactive_connection_is_refused_before_the_host(world):
    _grant(world)
    world.connection.is_active = False
    world.connection.save(update_fields=["is_active", "updated_at", "version"])
    with _tenant(world):
        assert _code(_create(world)) == "CONNECTION_INACTIVE"
    assert world.host.calls == []


@pytest.mark.parametrize("which", ["foreign", "other_personal", "absent"])
def test_another_orgs_or_users_connection_is_not_found(world, which):
    _grant(world)
    target = SimpleNamespace(guid=uuid.uuid4()) if which == "absent" else getattr(world, which)
    with _tenant(world):
        assert _code(_create(world, connection=target)) == "NOT_FOUND"
    assert world.host.calls == []


def test_org_repo_policy_refuses_unconnected_owners(world, monkeypatch):
    _grant(world)
    monkeypatch.setattr("astrolift_scm.org_repo_policy.rejection_reason", lambda repo, org: "no")
    with _tenant(world):
        assert _code(_create(world)) == "OWNER_NOT_ALLOWED"
    assert world.host.calls == []


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"visibility": "secret"}, "visibility"),
        ({"name": "../etc"}, "name"),
        ({"name": "repo.git"}, "name"),
        ({"owner": "a/b"}, "owner"),
        ({"owner": ""}, "owner"),
    ],
)
def test_input_is_validated_before_the_host(world, overrides, field):
    _grant(world)
    with _tenant(world):
        result = _create(world, **overrides)
    assert _code(result) == "VALIDATION" and result.errors[0].field == field
    assert world.host.calls == []


@pytest.mark.parametrize("permissions", [[Permission.SCM_READ, Permission.SCM_CONNECT], []])
def test_without_scm_write_at_the_org_it_is_denied(world, permissions):
    if permissions:
        _grant(world, *permissions)
    with _tenant(world):
        assert _code(_create(world)) == "PERMISSION_DENIED"
    assert world.host.calls == []
    assert world.audits[-1].decision == "DENY"


def test_a_team_grant_of_scm_write_does_not_reach_org_connections(world):
    _grant(world, kind="TEAM")
    with _tenant(world):
        assert _code(_create(world)) == "PERMISSION_DENIED"
    assert world.host.calls == []


def test_org_owner_role_can_create(world):
    role = Role.objects.get(slug="org_owner", is_system=True, organization=None)
    assert "scm.write" in role.permissions
    RoleBinding.objects.create(user=world.user, role=role, scope_kind="ORG", scope_id=world.org.pk)
    with _tenant(world):
        assert _create(world).ok


# --- bearer tokens -----------------------------------------------------------


@pytest.mark.parametrize("scopes", [[], ["scm:read"], ["write:apps", "app:onboard"]])
def test_token_without_scm_write_is_denied(world, scopes):
    _grant(world)
    with _tenant(world), _bearer(world, scopes):
        assert _code(_create(world)) == "PERMISSION_DENIED"
    assert world.host.calls == []


def test_token_with_scm_write_creates_in_its_own_org(world):
    _grant(world)
    with _tenant(world), _bearer(world, ["scm:write"]):
        assert _create(world).ok


@pytest.mark.parametrize("kind", ["foreign", "team"])
def test_scm_write_token_of_another_org_or_a_team_is_denied(world, kind):
    _grant(world)
    with _tenant(world), _bearer(world, ["scm:write"], foreign=kind == "foreign", team=kind == "team"):
        assert _code(_create(world)) == "PERMISSION_DENIED"
    assert world.host.calls == []


def test_scm_write_token_cannot_exceed_its_owners_roles(world):
    _grant(world, Permission.SCM_READ)
    with _tenant(world), _bearer(world, ["scm:write"]):
        assert _code(_create(world)) == "PERMISSION_DENIED"


def test_scm_read_token_lists_connections_and_repos_of_its_org(world):
    _grant(world, Permission.SCM_READ)
    info = make_info(world.user)
    with _tenant(world), _bearer(world, ["scm:read"]):
        rows = ScmQuery().astrolift_source_connections(info)
        assert str(world.connection.guid) in {str(r.id) for r in rows}
        assert str(world.foreign.guid) not in {str(r.id) for r in rows}
        repos = ScmQuery().astrolift_available_repos(info, connection_id=str(world.connection.guid))
        assert repos.error_code is None
        foreign = ScmQuery().astrolift_available_repos(info, connection_id=str(world.foreign.guid))
        assert foreign.error_code == "NOT_FOUND"


@pytest.mark.parametrize("scopes,foreign", [([], False), (["read:apps"], False), (["scm:read"], True)])
def test_scm_reads_are_denied_without_scm_read_in_the_tokens_org(world, scopes, foreign):
    _grant(world, Permission.SCM_READ)
    info = make_info(world.user)
    with _tenant(world), _bearer(world, scopes, foreign=foreign):
        with pytest.raises(PermissionDenied):
            ScmQuery().astrolift_available_repos(info, connection_id=str(world.connection.guid))


def test_scm_read_token_does_not_open_any_scope_collections(world):
    _grant(world, Permission.SCM_READ)
    with _tenant(world), _bearer(world, ["scm:read"]):
        with pytest.raises(PermissionDenied):
            ScmQuery().astrolift_ssh_deploy_keys(make_info(world.user))


def test_scope_reach_is_the_tokens_own_org_only():
    token = SimpleNamespace(organization_id=7, scopes=["scm:read", "scm:write"])
    own = PermissionScope(kind=ScopeKind.ORG, id=7)
    assert token_scope_reaches(token, "scm.read", own)
    assert token_scope_reaches(token, "scm.write", own)
    assert not token_scope_reaches(token, "scm.read", None)
    assert not token_scope_reaches(token, "scm.read", PermissionScope(kind=ScopeKind.ORG, id=8))
    assert not token_scope_reaches(token, "scm.write", PermissionScope(kind=ScopeKind.TEAM, id=7))
    assert token_scope_reaches(token, "app.read", None)
    assert token_scope_reaches(SimpleNamespace(organization_id=7, scopes=["admin"]), "scm.read", None)


def test_scopes_are_catalogued_and_map_to_one_permission_each():
    assert {"scm:read", "scm:write"} <= ALLOWED_SCOPES
    assert {"scm:read", "scm:write"} <= {s.value for s in SCOPE_CATALOG}
    assert permissions_for_scopes(["scm:read"]) == {"scm.read"}
    assert permissions_for_scopes(["scm:write"]) == {"scm.write"}


def test_web_url_follows_the_connection_host():
    def conn(kind, base=""):
        return SimpleNamespace(kind=kind, api_base_url=base)

    assert repo_writes.repository_web_url(conn("github_pat"), "a/b") == "https://github.com/a/b"
    assert (
        repo_writes.repository_web_url(conn("github_pat", "https://ghe.test/api/v3"), "a/b")
        == "https://ghe.test/a/b"
    )
    assert repo_writes.repository_web_url(conn("gitlab_pat"), "a/b") == "https://gitlab.com/a/b"
    assert (
        repo_writes.repository_web_url(conn("gitea_pat", "https://git.test/"), "a/b")
        == "https://git.test/a/b"
    )
    assert repo_writes.repository_web_url(conn("bitbucket_pat"), "a/b") == "https://bitbucket.org/a/b"


# --- the host drivers ----------------------------------------------------------


def _recorder(answers):
    from astrolift_registry.tests.test_brain_scaffold_2424 import Recorder

    return Recorder(answers)


def test_github_create_sends_description_and_auto_init(monkeypatch):
    monkeypatch.setattr(repo_writes, "_gh", lambda c: ("https://api.github.test", {}))
    rec = _recorder(
        {
            ("GET", "/user"): {"login": "someone"},
            ("POST", "/orgs/acme/repos"): {"full_name": "acme/kb"},
            ("GET", "/repos/acme/kb"): {"default_branch": "main", "permissions": {"push": True}},
            ("GET", "/repos/acme/kb/commits"): [],
        }
    )
    monkeypatch.setattr(repo_writes, "_http", rec)
    state = repo_writes.create_repository(
        SimpleNamespace(kind="github_pat"), "acme", "kb", "internal", description="d", auto_init=False
    )
    body = next(b for m, u, b in rec.calls if m == "POST")
    assert body == {
        "name": "kb",
        "private": True,
        "auto_init": False,
        "description": "d",
        "visibility": "internal",
    }
    assert state.writable is True


@pytest.mark.parametrize(
    "permissions,writable",
    [
        ({"push": True}, True),
        ({"admin": True, "push": False}, True),
        ({"pull": True, "push": False}, False),
        (None, None),
    ],
)
def test_github_state_reports_whether_the_connection_can_push(monkeypatch, permissions, writable):
    monkeypatch.setattr(repo_writes, "_gh", lambda c: ("https://api.github.test", {}))
    meta = {"default_branch": "main", **({"permissions": permissions} if permissions is not None else {})}
    monkeypatch.setattr(
        repo_writes,
        "_http",
        _recorder(
            {("GET", "/repos/acme/kb"): meta, ("GET", "/repos/acme/kb/commits"): [{"sha": "a"}, {"sha": "b"}]}
        ),
    )
    assert repo_writes.repository_state(SimpleNamespace(kind="github_pat"), "acme/kb").writable is writable


def test_github_create_keeps_its_initial_commit_by_default(monkeypatch):
    monkeypatch.setattr(repo_writes, "_gh", lambda c: ("https://api.github.test", {}))
    rec = _recorder(
        {
            ("GET", "/user"): {"login": "acme"},
            ("POST", "/user/repos"): {"full_name": "acme/kb"},
            ("GET", "/repos/acme/kb"): {"default_branch": "main"},
            ("GET", "/repos/acme/kb/commits"): [],
        }
    )
    monkeypatch.setattr(repo_writes, "_http", rec)
    repo_writes.create_repository(SimpleNamespace(kind="github_pat"), "acme", "kb", "private")
    body = next(b for m, u, b in rec.calls if m == "POST")
    assert body == {"name": "kb", "private": True, "auto_init": True}
