"""astroliftSourceConnectionsPage / astroliftSshDeployKeysPage (#1235).

Both list fields behind /settings/source-providers sliced at 200 rows and
offered no way to reach the 201st. An org that onboards operators through
per-user OAuth accumulates one connection row per human, and an org that
mints a per-app keypair at registration accumulates one key row per app —
so on a real fleet the cap doesn't degrade the page, it hides rows. These
tests are what stop that coming back.

The two page fields share their queryset builder with the list fields they
deprecate, so "which rows exist" cannot drift between them; several tests
below pin that directly. Real Postgres rows throughout.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection, SshDeployKey
from astrolift_scm.schema.queries import ScmQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures + factories
# ---------------------------------------------------------------------------


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-scm-pages")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Rival", slug="rival-scm-pages")


@pytest.fixture
def viewer():
    return User.objects.create(username="scm-pages-viewer", email="viewer@acme.test")


@pytest.fixture
def other_user():
    return User.objects.create(username="scm-pages-colleague", email="colleague@acme.test")


@pytest.fixture
def app(org):
    team = Team.objects.create(organization=org, name="Platform", slug="platform-scm-pages")
    project = Project.objects.create(organization=org, team=team, name="Core", slug="core-scm-pages")
    return RegisteredApp.objects.create(
        organization=org, project=project, team=team, name="Billing API", slug="billing-api"
    )


def _info(user=None):
    """Shape the resolvers read: ``info.context.request.user``."""
    request = SimpleNamespace(user=user) if user is not None else None
    return SimpleNamespace(context=SimpleNamespace(request=request))


def _conn(org, login, *, kind=SourceConnection.Kind.GITHUB_PAT, **kwargs):
    return SourceConnection.objects.create(
        organization=org,
        kind=kind,
        account_login=login,
        is_active=kwargs.pop("is_active", True),
        **kwargs,
    )


def _key(org, name, *, fingerprint=None, **kwargs):
    return SshDeployKey.objects.create(
        organization=org,
        name=name,
        public_key=f"ssh-ed25519 AAAA{name}",
        fingerprint_sha256=fingerprint or f"SHA256:{name}",
        private_key_ciphertext=b"ciphertext",
        **kwargs,
    )


def _walk_connections(query, org, info, *, limit, **kwargs):
    """Page through the whole connection stream, newest first."""
    logins: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(60):  # bounded so a non-terminating walk fails loudly
            page = query.astrolift_source_connections_page(info, limit=limit, after=cursor, **kwargs)
            logins.extend(c.account_login for c in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return logins
    raise AssertionError("connections walk did not terminate")


def _walk_keys(query, org, info, *, limit, **kwargs):
    names: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(60):
            page = query.astrolift_ssh_deploy_keys_page(info, limit=limit, after=cursor, **kwargs)
            names.extend(k.name for k in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return names
    raise AssertionError("deploy-key walk did not terminate")


# ---------------------------------------------------------------------------
# astroliftSourceConnectionsPage
# ---------------------------------------------------------------------------


def test_connections_page_reaches_past_the_old_two_hundred_row_cap(org, viewer, permission_resolver):
    """The regression that motivated the epic: with the list field, the
    201st connection did not exist as far as the UI was concerned."""
    permission_resolver.grant(Permission.SCM_READ)
    SourceConnection.objects.bulk_create(
        [
            SourceConnection(
                organization=org,
                kind=SourceConnection.Kind.GITHUB_PAT,
                account_login=f"bot-{n:03d}",
                is_active=True,
            )
            for n in range(205)
        ]
    )

    query = ScmQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = query.astrolift_source_connections(_info(viewer))
    assert len(capped) == 200, "precondition: the list field still caps"

    logins = _walk_connections(query, org, _info(viewer), limit=50)
    assert len(logins) == 205
    assert len(set(logins)) == 205, "a row was served twice"


def test_connections_walk_is_newest_first_and_loses_nothing(org, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    for n in range(7):
        _conn(org, f"bot-{n}")

    expected = list(
        SourceConnection.objects.filter(organization=org)
        .order_by("-created_at", "-guid")
        .values_list("account_login", flat=True)
    )
    assert _walk_connections(ScmQuery(), org, _info(viewer), limit=3) == expected


def test_connections_total_count_is_the_whole_result_set(org, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    for n in range(12):
        _conn(org, f"bot-{n}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_source_connections_page(_info(viewer), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_connections_search_matches_kind_url_login_and_display_name(org, viewer, permission_resolver):
    """The same four fields the settings table joined for its client-side
    filter, so moving the box server-side doesn't change what matches."""
    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "octo-bot", display_name="Primary GitHub")
    _conn(
        org,
        "release-bot",
        kind=SourceConnection.Kind.GITLAB_PAT,
        display_name="Self-hosted",
        api_base_url="https://git.internal.example/api/v4",
    )
    _conn(org, "tea-bot", kind=SourceConnection.Kind.GITEA_PAT, display_name="Tea Party")

    query = ScmQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_kind = query.astrolift_source_connections_page(_info(viewer), search="gitlab")
        by_url = query.astrolift_source_connections_page(_info(viewer), search="internal.example")
        by_login = query.astrolift_source_connections_page(_info(viewer), search="octo")
        by_display = query.astrolift_source_connections_page(_info(viewer), search="Tea Party")

    assert [c.account_login for c in by_kind.items] == ["release-bot"]
    assert [c.account_login for c in by_url.items] == ["release-bot"]
    assert [c.account_login for c in by_login.items] == ["octo-bot"]
    assert [c.account_login for c in by_display.items] == ["tea-bot"]


def test_connections_search_narrows_total_count_not_just_the_page(org, viewer, permission_resolver):
    """A count that ignored the search would render "3 results" over a
    one-row table."""
    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "keep-me")
    _conn(org, "other-1")
    _conn(org, "other-2")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_source_connections_page(_info(viewer), search="keep-me")
    assert [c.account_login for c in page.items] == ["keep-me"]
    assert page.total_count == 1


def test_other_orgs_connections_are_invisible(org, other_org, viewer, permission_resolver):
    """SourceConnection's default manager is not tenant-aware, so the org
    scope is the resolver's job (#1183). The viewer's OWN token minted in
    another org must stay behind that boundary too."""
    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "ours")
    _conn(other_org, "theirs")
    _conn(other_org, "viewer-elsewhere", user=viewer, kind=SourceConnection.Kind.GITHUB_OAUTH_USER)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_source_connections_page(_info(viewer), limit=50)
    assert [c.account_login for c in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked another org's rows"


def test_another_operators_personal_token_never_appears(org, viewer, other_user, permission_resolver):
    """A personal token belongs to the user that minted it. Org-level rows
    are shared; a colleague's OAuth row is not."""
    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "shared-pat")
    _conn(org, "mine", user=viewer, kind=SourceConnection.Kind.GITHUB_OAUTH_USER)
    _conn(org, "theirs", user=other_user, kind=SourceConnection.Kind.GITLAB_OAUTH_USER)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_source_connections_page(_info(viewer), limit=50)
    assert sorted(c.account_login for c in page.items) == ["mine", "shared-pat"]
    assert page.total_count == 2, "the count leaked a colleague's personal token"


def test_anonymous_caller_sees_org_level_connections_only(org, viewer, permission_resolver):
    """No authenticated viewer → no personal rows, not every personal row."""
    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "shared-pat")
    _conn(org, "mine", user=viewer, kind=SourceConnection.Kind.GITHUB_OAUTH_USER)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_source_connections_page(_info(None), limit=50)
    assert [c.account_login for c in page.items] == ["shared-pat"]
    assert page.total_count == 1


def test_disconnected_connections_leave_the_page(org, viewer, permission_resolver):
    """Disconnect soft-deletes the row; the page must drop it like the
    list field does — both read the same builder."""
    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "live")
    gone = _conn(org, "gone")
    gone.deleted_at = timezone.now()
    gone.save(update_fields=["deleted_at"])

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_source_connections_page(_info(viewer), limit=50)
    assert [c.account_login for c in page.items] == ["live"]
    assert page.total_count == 1


def test_connections_page_and_list_field_agree_on_the_row_set(org, viewer, other_user, permission_resolver):
    """The shared queryset builder is the point: the deprecated field and
    its replacement must never disagree about which rows exist."""
    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "shared-pat")
    _conn(org, "mine", user=viewer, kind=SourceConnection.Kind.GITHUB_OAUTH_USER)
    _conn(org, "theirs", user=other_user, kind=SourceConnection.Kind.GITLAB_OAUTH_USER)

    query = ScmQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        listed = query.astrolift_source_connections(_info(viewer))
    walked = _walk_connections(query, org, _info(viewer), limit=2)
    assert sorted(c.account_login for c in listed) == sorted(walked)


def test_connections_page_refuses_a_missing_tenant_context(org, viewer, permission_resolver):
    """Fails closed rather than paging every org's connections (#1183).

    ``@tenant_scoped`` rejects first; ``_source_connections_qs`` filtering
    on ``organization_id=None`` matches nothing if it is ever reached.
    """
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.SCM_READ)
    _conn(org, "ours")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            ScmQuery().astrolift_source_connections_page(_info(viewer), limit=50)


# ---------------------------------------------------------------------------
# astroliftSshDeployKeysPage
# ---------------------------------------------------------------------------


def test_keys_page_reaches_past_the_old_two_hundred_row_cap(org, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    SshDeployKey.objects.bulk_create(
        [
            SshDeployKey(
                organization=org,
                name=f"key-{n:03d}",
                public_key=f"ssh-ed25519 AAAA{n:03d}",
                fingerprint_sha256=f"SHA256:{n:03d}",
                private_key_ciphertext=b"ciphertext",
            )
            for n in range(205)
        ]
    )

    query = ScmQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = query.astrolift_ssh_deploy_keys(_info(viewer))
    assert len(capped) == 200, "precondition: the list field still caps"

    names = _walk_keys(query, org, _info(viewer), limit=50)
    assert len(names) == 205
    assert len(set(names)) == 205, "a row was served twice"


def test_keys_walk_is_newest_first_and_loses_nothing(org, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    for n in range(7):
        _key(org, f"key-{n}")

    expected = list(
        SshDeployKey.objects.filter(organization=org)
        .order_by("-created_at", "-guid")
        .values_list("name", flat=True)
    )
    assert _walk_keys(ScmQuery(), org, _info(viewer), limit=3) == expected


def test_keys_total_count_is_the_whole_result_set(org, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    for n in range(12):
        _key(org, f"key-{n}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_ssh_deploy_keys_page(_info(viewer), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_keys_app_slug_selects_that_apps_keys(org, app, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "org-wide")
    _key(org, "app-scoped", registered_app=app)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_ssh_deploy_keys_page(_info(viewer), app_slug=app.slug)
    assert [k.name for k in page.items] == ["app-scoped"]
    assert page.total_count == 1


def test_keys_empty_app_slug_selects_org_scoped_only(org, app, viewer, permission_resolver):
    """``appSlug: ""`` is a sentinel, not "no filter" — GraphQL can tell it
    apart from null and the picker relies on the distinction."""
    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "org-wide")
    _key(org, "app-scoped", registered_app=app)

    query = ScmQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        scoped = query.astrolift_ssh_deploy_keys_page(_info(viewer), app_slug="")
        unfiltered = query.astrolift_ssh_deploy_keys_page(_info(viewer), app_slug=None)

    assert [k.name for k in scoped.items] == ["org-wide"]
    assert scoped.total_count == 1
    assert sorted(k.name for k in unfiltered.items) == ["app-scoped", "org-wide"]
    assert unfiltered.total_count == 2


def test_keys_search_matches_name_fingerprint_and_app_slug(org, app, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "shared-org-key", fingerprint="SHA256:AAA111")
    _key(org, "deploy", fingerprint="SHA256:BBB222", registered_app=app)

    query = ScmQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_name = query.astrolift_ssh_deploy_keys_page(_info(viewer), search="shared")
        by_fingerprint = query.astrolift_ssh_deploy_keys_page(_info(viewer), search="BBB222")
        by_app = query.astrolift_ssh_deploy_keys_page(_info(viewer), search="billing-api")

    assert [k.name for k in by_name.items] == ["shared-org-key"]
    assert [k.name for k in by_fingerprint.items] == ["deploy"]
    assert [k.name for k in by_app.items] == ["deploy"]


def test_keys_search_narrows_total_count_not_just_the_page(org, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "keep-me")
    _key(org, "other-1")
    _key(org, "other-2")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_ssh_deploy_keys_page(_info(viewer), search="keep-me")
    assert [k.name for k in page.items] == ["keep-me"]
    assert page.total_count == 1


def test_keys_search_composes_with_the_app_filter(org, app, viewer, permission_resolver):
    """Both narrowings apply; the filter is not replaced by the search."""
    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "rotate-me", fingerprint="SHA256:ORG")
    _key(org, "rotate-me-too", fingerprint="SHA256:APP", registered_app=app)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_ssh_deploy_keys_page(_info(viewer), app_slug="", search="rotate")
    assert [k.name for k in page.items] == ["rotate-me"]
    assert page.total_count == 1


def test_other_orgs_keys_are_invisible(org, other_org, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "ours")
    _key(other_org, "theirs")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = ScmQuery().astrolift_ssh_deploy_keys_page(_info(viewer), limit=50)
    assert [k.name for k in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked another org's rows"


def test_keys_page_and_list_field_agree_on_the_row_set(org, app, viewer, permission_resolver):
    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "org-wide")
    _key(org, "app-scoped", registered_app=app)
    _key(org, "another-org-wide")

    query = ScmQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        listed = query.astrolift_ssh_deploy_keys(_info(viewer))
    walked = _walk_keys(query, org, _info(viewer), limit=2)
    assert sorted(k.name for k in listed) == sorted(walked)


def test_keys_page_refuses_a_missing_tenant_context(org, viewer, permission_resolver):
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.SCM_READ)
    _key(org, "ours")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            ScmQuery().astrolift_ssh_deploy_keys_page(_info(viewer), limit=50)
