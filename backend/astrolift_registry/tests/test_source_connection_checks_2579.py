"""Registration refuses a source repo the platform cannot read, and the app
detail reports the source connection for the repo's owner (#2579).

Real Postgres. GitHub is stubbed at its HTTP boundary (the installation-token
mint, ``urlopen`` and the anonymous raw read), as the other SCM tests do;
autowire's repo writes have their own coverage and are stubbed out.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
import requests

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

SHA = "cd" * 20
MANIFEST = """name = "cykick"
[[workloads]]
name = "web"
kind = "deployment"
[[workloads.containers]]
name = "app"
is_primary = true
image_ref = "nginx:1.27.5"
"""
REMEDY = (
    "Add a GitHub connection for {owner} under Providers → Source: Connect to GitHub, or Connect an "
    "existing GitHub App with {owner} as the GitHub org / user login. To register before the connection "
    "exists, send the manifest with the registration (astro app register --manifest-raw)."
)
NO_CONNECTION = (
    "This organization has no GitHub connection, so Astrolift cannot fetch, build or receive webhooks "
    "for {owner}'s repositories. Connect GitHub under Providers → Source."
)
NOT_ON_OWNER = (
    "No GitHub App installation in this organization is recorded on {owner}. If fetches, builds or "
    "webhooks for this repository fail, add a GitHub connection for {owner} under Providers → Source: "
    "Connect to GitHub, or Connect an existing GitHub App with {owner} as the GitHub org / user login."
)


@pytest.fixture(autouse=True)
def _no_autowire_writes(monkeypatch):
    monkeypatch.setattr("astrolift_scm.services.autowire.run_autowire", lambda app, actor=None: None)


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def github(monkeypatch):
    """GitHub through an App installation. ``commit`` is the revision read's
    answer: None for the commit, an HTTP status, or an exception to raise.
    Every requested URL is recorded."""
    state = SimpleNamespace(commit=None, calls=[])
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token", lambda connection: "ghs_fake"
    )

    def urlopen(request, timeout=None):
        url = request.full_url
        state.calls.append(url)
        if "/commits/" in url:
            if state.commit is None:
                return io.BytesIO(json.dumps({"sha": SHA}).encode())
            if isinstance(state.commit, Exception):
                raise state.commit
            raise urllib.error.HTTPError(url, state.commit, "refused", {}, None)
        if "/contents/" in url:
            return io.BytesIO(MANIFEST.encode())
        raise AssertionError(f"unexpected GitHub call {url}")

    monkeypatch.setattr("astrolift_scm.providers.revisions.urllib.request.urlopen", urlopen)
    return state


@pytest.fixture
def anonymous(monkeypatch):
    """The anonymous read of a public repo (#2051): ``text`` is what it finds,
    None for GitHub's 404; ``error`` is raised instead when set. Every read is
    recorded."""
    state = SimpleNamespace(text=None, error=None, reads=[])

    def read(*, repo_full_name, path, ref):
        state.reads.append((repo_full_name, path, ref))
        if state.error is not None:
            raise state.error
        return state.text

    monkeypatch.setattr("astrolift_scm.providers.repo_tree.fetch_public_file", read)
    return state


def _org(seed_cluster, slug):
    org = Organization.objects.create(name="Acme", slug=f"acme-{slug}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{slug}")
    seed_cluster(org, slug=f"k-{slug}")
    return org, project


def _installation(org, login):
    encrypted = encrypt_at_rest(b"fake-pem")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name=f"GitHub App: {login}",
        account_login=login,
        installation_id="987",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _register(org, project, source_repo, **fields):
    with tenant_context(TenantContext(organization_id=org.id)):
        return RegistryMutation().register_app(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            input=RegisterAppInput(
                project_id=str(project.guid), name="Cykick", slug="cykick", source_repo=source_repo, **fields
            ),
        )


def _refusal(result):
    assert not result.ok
    [error] = result.errors
    assert (error.code, error.field) == ("PRECONDITION", "sourceRepo")
    return error.message


# ---------------------------------------------------------------------------
# registerApp refuses a repo the platform cannot read
# ---------------------------------------------------------------------------


def test_a_private_repo_on_an_owner_with_no_connection_is_refused_before_anything_is_created(
    github, anonymous, permission_resolver, seed_cluster
):
    """No connection serves a repo whose owner none is recorded on (#2576), so
    the ConflictHQ installation is never asked: the anonymous read a deploy's
    clone would make decides."""
    org, project = _org(seed_cluster, "refused")
    _installation(org, "ConflictHQ")
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "calliopeai/cykick")

    assert _refusal(result) == (
        "Astrolift cannot read calliopeai/cykick: no GitHub connection in this organization is recorded on "
        "calliopeai, and astrolift.toml on main could not be read without one (the repository is private, "
        "or the file is not there). " + REMEDY.format(owner="calliopeai")
    )
    assert not RegisteredApp.all_objects.filter(organization=org).exists()
    assert github.calls == []
    assert anonymous.reads == [("calliopeai/cykick", "astrolift.toml", "main")]


def test_the_deploy_branch_is_the_ref_read(github, anonymous, permission_resolver, seed_cluster):
    org, project = _org(seed_cluster, "release")
    _installation(org, "ConflictHQ")
    permission_resolver.grant(Permission.APP_CREATE)

    message = _refusal(_register(org, project, "calliopeai/cykick", deploy_branch="release"))

    assert "astrolift.toml on release could not be read without one" in message
    assert not RegisteredApp.all_objects.filter(organization=org).exists()
    assert anonymous.reads == [("calliopeai/cykick", "astrolift.toml", "release")]
    assert github.calls == []


def test_a_repo_with_no_connection_and_no_anonymous_read_is_refused(
    anonymous, permission_resolver, seed_cluster
):
    org, project = _org(seed_cluster, "private")
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "acme/private-app", manifest_path="apps/web/astrolift.toml")

    assert _refusal(result) == (
        "Astrolift cannot read acme/private-app: this organization has no GitHub connection, and "
        "apps/web/astrolift.toml on main could not be read without one (the repository is private, or the "
        "file is not there). " + REMEDY.format(owner="acme")
    )
    assert not RegisteredApp.all_objects.filter(organization=org).exists()
    assert anonymous.reads == [("acme/private-app", "apps/web/astrolift.toml", "main")]


# ---------------------------------------------------------------------------
# ...and goes ahead, with the advisory, wherever that is not proven
# ---------------------------------------------------------------------------


def test_a_public_repo_registers_through_no_other_accounts_installation(
    github, anonymous, permission_resolver, seed_cluster
):
    """An installation recorded on another account is not asked to confirm
    a read (#2576): a repo goes ahead, with the warning, when it reads
    anonymously, as a deploy's clone of it would."""
    org, project = _org(seed_cluster, "stale-label")
    _installation(org, "ConflictHQ")
    anonymous.text = MANIFEST
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "calliopeai/cykick")

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(organization=org, slug="cykick")
    assert app.manifest_bootstrap_status == "applied"
    assert result.data.source_connection_warning == NOT_ON_OWNER.format(owner="calliopeai")
    assert github.calls == []


@pytest.mark.parametrize(
    "error",
    [
        requests.HTTPError("429 Too Many Requests"),
        requests.HTTPError("500 Internal Server Error"),
        requests.ConnectionError("down"),
    ],
    ids=["rate-limited", "server-error", "unreachable"],
)
def test_a_host_that_gives_no_verdict_lets_registration_proceed(
    github, anonymous, permission_resolver, seed_cluster, error
):
    org, project = _org(seed_cluster, "no-verdict")
    _installation(org, "ConflictHQ")
    anonymous.error = error
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "calliopeai/cykick")

    assert result.ok, result.errors
    assert RegisteredApp.objects.filter(organization=org, slug="cykick").exists()
    assert result.data.source_connection_warning == NOT_ON_OWNER.format(owner="calliopeai")
    # The check's own read; the manifest bootstrap that follows reads again.
    assert anonymous.reads[0] == ("calliopeai/cykick", "astrolift.toml", "main")
    assert github.calls == []


def test_a_public_repo_registers_without_a_connection(anonymous, permission_resolver, seed_cluster):
    org, project = _org(seed_cluster, "public")
    anonymous.text = MANIFEST
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "acme/public-app")

    assert result.ok, result.errors
    assert RegisteredApp.objects.get(organization=org, slug="cykick").manifest_bootstrap_status == "applied"
    assert result.data.source_connection_warning == NO_CONNECTION.format(owner="acme")


def test_an_inline_manifest_registers_before_a_connection_exists(
    anonymous, permission_resolver, seed_cluster
):
    """astrolift-cli#90: the inline manifest is how a fresh org registers an
    app the platform cannot read yet, so it gets the warning, not a refusal."""
    org, project = _org(seed_cluster, "inline")
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "acme/private-app", manifest_raw=MANIFEST)

    assert result.ok, result.errors
    assert RegisteredApp.objects.filter(organization=org, slug="cykick").exists()
    assert result.data.source_connection_warning == NO_CONNECTION.format(owner="acme")
    assert anonymous.reads == []


def test_a_connection_recorded_on_the_owner_is_trusted_without_a_read(
    github, permission_resolver, seed_cluster
):
    org, project = _org(seed_cluster, "on-owner")
    _installation(org, "CalliopeAI")
    github.commit = 404
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "calliopeai/cykick")

    assert result.ok, result.errors
    assert result.data.source_connection_warning == ""
    assert not [url for url in github.calls if "/commits/" in url]


def test_a_repo_on_another_host_is_not_checked(anonymous, permission_resolver, seed_cluster):
    org, project = _org(seed_cluster, "gitlab")
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "group/app", source_kind="gitlab")

    assert result.ok, result.errors
    assert anonymous.reads == []


# ---------------------------------------------------------------------------
# astroliftApp reports the source connection for the repo's owner
# ---------------------------------------------------------------------------

APP_DETAIL = """
query($slug: String!) {
  astroliftApp(slug: $slug) { slug sourceConnection { state owner detail } }
}
"""
APP_LIST = "query { astroliftApps { slug sourceConnection { state } } }"


@pytest.fixture
def world():
    world = ScopeWorld("conn2579")
    world.user = make_user("conn2579")
    return world


def _read(world, query, variables=None):
    from config.schema import schema

    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    ):
        return schema.execute_sync(
            query, variable_values=variables, context_value=make_info(world.user).context
        )


def _can_read(world):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )


@pytest.mark.parametrize(
    "source_kind,source_repo,login,expected",
    [
        ("github", "", None, ("UNKNOWN", "", "The app has no source repository.")),
        ("github", "no-owner", None, ("UNKNOWN", "", "The app's source repository names no owner.")),
        (
            "gitlab",
            "group/app",
            None,
            ("UNKNOWN", "group", "Astrolift checks source connections for GitHub repositories only."),
        ),
        ("github", "calliopeai/cykick", None, ("NOT_CONNECTED", "calliopeai", NO_CONNECTION)),
        ("github", "calliopeai/cykick", "ConflictHQ", ("NOT_CONNECTED", "calliopeai", NOT_ON_OWNER)),
        ("github", "CalliopeAI/cykick", "calliopeai", ("CONNECTED", "calliopeai", "")),
    ],
    ids=["no-repo", "no-owner", "gitlab", "no-connection", "other-account", "on-owner"],
)
def test_app_detail_reports_the_source_connection_for_the_repo_owner(
    world, source_kind, source_repo, login, expected
):
    _can_read(world)
    app = world.medops_app
    app.source_kind = source_kind
    app.source_repo = source_repo
    app.save()
    if login:
        _installation(world.org, login)

    result = _read(world, APP_DETAIL, {"slug": app.slug})

    assert result.errors is None, result.errors
    found = result.data["astroliftApp"]["sourceConnection"]
    state, owner, detail = expected
    assert (found["state"], found["owner"], found["detail"]) == (state, owner, detail.format(owner=owner))


def test_list_reads_leave_the_source_connection_null(world):
    _can_read(world)

    result = _read(world, APP_LIST)

    assert result.errors is None, result.errors
    rows = result.data["astroliftApps"]
    assert world.medops_app.slug in {row["slug"] for row in rows}
    assert all(row["sourceConnection"] is None for row in rows)


def test_reading_the_source_connection_needs_app_read(world):
    result = _read(world, APP_DETAIL, {"slug": world.medops_app.slug})

    assert result.errors, "a caller without app.read must be refused"
    assert "app.read" in result.errors[0].message
    assert (result.data or {}).get("astroliftApp") is None
