"""#1920: raw manifest text must mask ``[env]`` values unless the
caller holds ``secret.read`` and is step-up elevated, the same gate
``revealAppSecret`` enforces.

Covers the ``RegisteredApp`` GraphQL type (single-app + list queries,
which take two different code paths inside ``app_to_type``: a fresh
per-app RBAC lookup vs. a pre-resolved bulk ``viewerPermissions`` map)
and the manifest-stage mutation that echoes raw text directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model

from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_identity.session_elevation import METHOD_PASSWORD, elevate
from astrolift_manifest.env_edit import REDACTED_ENV_VALUE
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.types import UpdateManifestInput
from astrolift_registry.schema.queries import RegistryQuery
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_SECRET_VALUE = "sk-live-super-secret-123"

_BASE_TOML = f"""\
astrolift_version = 1
name = "hello"

[env]
API_KEY = "{_SECRET_VALUE}"
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "deployment"
"""

_STAGED_TOML = f"""\
astrolift_version = 1
name = "hello"

[env]
API_KEY = "{_SECRET_VALUE}"
LOG_LEVEL = "debug"

[[workloads]]
name = "web"
kind = "deployment"
"""


class _FakeSession(dict):
    """Dict-like stand-in for ``HttpRequest.session`` (mirrors the
    step-up test harness's shim)."""

    modified = False


def _info(user, *, session: dict | None = None):
    session = session if session is not None else _FakeSession()
    request = SimpleNamespace(user=user, session=session, META={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _make_user(username: str) -> object:
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _grant(user, org, *permissions: str) -> None:
    """Real Role + org-scoped RoleBinding. ``resolve_effective_permissions``
    reads RoleBinding rows directly, so a stubbed permission resolver
    (as ``test_secrets_mutations.py`` uses) would be invisible to it."""
    role = Role.objects.create(
        name=f"role-{'-'.join(permissions)}-{user.pk}",
        slug=f"role-{'-'.join(permissions)}-{user.pk}",
        permissions=list(permissions),
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        manifest_raw=_BASE_TOML,
    )
    return org, app


def _ctx(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


# ---- astroliftApp (single-app detail query) -----------------------


def test_astrolift_app_masks_env_values_for_app_read_only(seed_cluster):
    org, app = _scaffold()
    seed_cluster(org)
    viewer = _make_user("app-read-only")
    _grant(viewer, org, "app.read")

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer), slug=app.slug)

    assert result is not None
    assert _SECRET_VALUE not in result.raw_manifest
    assert "API_KEY" in result.raw_manifest
    assert REDACTED_ENV_VALUE in result.raw_manifest


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_astrolift_app_reveals_env_values_for_secret_read_elevated(seed_cluster):
    org, app = _scaffold()
    seed_cluster(org)
    viewer = _make_user("secret-read-elevated")
    _grant(viewer, org, "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer, session=session), slug=app.slug)

    assert result is not None
    assert _SECRET_VALUE in result.raw_manifest


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_astrolift_app_masks_for_secret_read_without_elevation(seed_cluster):
    """Holding secret.read is not enough on its own: the step-up half
    of the gate must also be satisfied, same as ``revealAppSecret``."""
    org, app = _scaffold()
    seed_cluster(org)
    viewer = _make_user("secret-read-unelevated")
    _grant(viewer, org, "app.read", "secret.read")

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer, session=_FakeSession()), slug=app.slug)

    assert result is not None
    assert _SECRET_VALUE not in result.raw_manifest


# ---- astroliftMyApps (list query, bulk viewerPermissions path) ---


def test_astrolift_my_apps_masks_env_values_for_app_read_only():
    org, app = _scaffold()
    viewer = _make_user("list-app-read-only")
    _grant(viewer, org, "app.read")

    with _ctx(org, viewer):
        results = RegistryQuery().astrolift_my_apps(_info(viewer))

    assert len(results) == 1
    assert _SECRET_VALUE not in results[0].raw_manifest
    assert "API_KEY" in results[0].raw_manifest


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_astrolift_my_apps_reveals_env_values_for_secret_read_elevated():
    org, app = _scaffold()
    viewer = _make_user("list-secret-read-elevated")
    _grant(viewer, org, "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, viewer):
        results = RegistryQuery().astrolift_my_apps(_info(viewer, session=session))

    assert len(results) == 1
    assert _SECRET_VALUE in results[0].raw_manifest


# ---- updateManifest mutation ---------------------------------------


def test_update_manifest_masks_env_values_for_app_update_only():
    """``app.update`` (not app.read/secret.read) is all this mutation
    requires to run, but its response must not become a side-door
    into every other secret already staged on the app (#1920)."""
    org, app = _scaffold()
    caller = _make_user("update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = RegistryMutation().update_manifest(
            _info(caller),
            input=UpdateManifestInput(id=GUID(str(app.guid)), raw_manifest=_STAGED_TOML),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert "API_KEY" in result.data.raw_manifest_staged
    app.refresh_from_db()
    # The fix is response-shaping only: the stored staged text is untouched.
    assert app.manifest_raw_staged == _STAGED_TOML


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_update_manifest_reveals_env_values_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("update-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = RegistryMutation().update_manifest(
            _info(caller, session=session),
            input=UpdateManifestInput(id=GUID(str(app.guid)), raw_manifest=_STAGED_TOML),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE in result.data.raw_manifest
    assert _SECRET_VALUE in result.data.raw_manifest_staged


# ---- masked read -> save round trip (#1920 review) ------------------

_OLD_SECRET = "sk-live-old-rotated-000"
_DB_SECRET = "pw-222"

_HAND_EDITED_TOML = f"""\
# hand-edited in the repo
astrolift_version = 1
name = "hello"   # display name

[env]
# previous key: {_OLD_SECRET}
API_KEY   = "{_SECRET_VALUE}"   # prod
DB_URL = 'postgres://app:{_DB_SECRET}@db/app'
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "deployment"
"""


def _stage(app, text: str) -> None:
    app.manifest_raw_staged = text
    app.save(update_fields=["manifest_raw_staged"])


def _masked_staged_read(org, app, caller) -> str:
    with _ctx(org, caller):
        read = RegistryQuery().astrolift_app(_info(caller), slug=app.slug)
    masked = read.raw_manifest_staged
    for secret in (_SECRET_VALUE, _OLD_SECRET, _DB_SECRET):
        assert secret not in masked
    return masked


def _save(org, app, caller, text: str):
    with _ctx(org, caller):
        return RegistryMutation().update_manifest(
            _info(caller),
            input=UpdateManifestInput(id=GUID(str(app.guid)), raw_manifest=text),
        )


def test_update_manifest_masked_round_trip_keeps_the_stored_text(seed_cluster):
    """Load the masked editor text and save it back unchanged. Before
    #1920's review that replaced every stored secret with the
    placeholder; now the stored document, comments included, is
    byte-identical afterwards."""
    org, app = _scaffold()
    seed_cluster(org)
    _stage(app, _HAND_EDITED_TOML)
    caller = _make_user("round-trip")
    _grant(caller, org, "app.read", "app.update")

    masked = _masked_staged_read(org, app, caller)
    result = _save(org, app, caller, masked)

    assert result.ok is True, result.errors
    app.refresh_from_db()
    assert app.manifest_raw_staged == _HAND_EDITED_TOML
    assert result.data.raw_manifest_staged == masked


def test_update_manifest_masked_save_keeps_secrets_next_to_an_edit(seed_cluster):
    org, app = _scaffold()
    seed_cluster(org)
    _stage(app, _HAND_EDITED_TOML)
    caller = _make_user("round-trip-edit")
    _grant(caller, org, "app.read", "app.update")

    masked = _masked_staged_read(org, app, caller)
    result = _save(org, app, caller, masked.replace('name = "web"', 'name = "api"'))

    assert result.ok is True, result.errors
    app.refresh_from_db()
    assert app.manifest_raw_staged == _HAND_EDITED_TOML.replace('name = "web"', 'name = "api"')


def test_update_manifest_rejects_a_placeholder_with_no_stored_value(seed_cluster):
    org, app = _scaffold()
    seed_cluster(org)
    _stage(app, _HAND_EDITED_TOML)
    caller = _make_user("round-trip-ghost")
    _grant(caller, org, "app.read", "app.update")

    masked = _masked_staged_read(org, app, caller)
    result = _save(
        org, app, caller, masked.replace("LOG_LEVEL", f'GHOST = "{REDACTED_ENV_VALUE}"\nLOG_LEVEL')
    )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "rawManifest"
    assert "GHOST" in result.errors[0].message
    app.refresh_from_db()
    assert app.manifest_raw_staged == _HAND_EDITED_TOML


# ---- registerApp -------------------------------------------------------


def _register(org, project, *, slug: str, manifest_raw: str):
    from astrolift_registry.schema.mutations import RegisterAppInput

    with tenant_context(TenantContext(organization_id=org.id)):
        return RegistryMutation().register_app(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Copied",
                slug=slug,
                source_repo=f"acme/{slug}",
                manifest_raw=manifest_raw,
            ),
        )


def test_register_app_rejects_a_masked_placeholder(permission_resolver, seed_cluster):
    """A manifest copied out of a masked read has nothing to restore
    from on a new app; storing it would make the placeholder the secret."""
    from astrolift_manifest.env_edit import redact_env_values
    from core.permissions import Permission

    org, app = _scaffold()
    seed_cluster(org)
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, app.project, slug="copied-app", manifest_raw=redact_env_values(_BASE_TOML))

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "manifestRaw"
    assert not RegisteredApp.objects.filter(slug="copied-app").exists()


def test_register_app_bootstrap_error_does_not_quote_the_manifest(
    monkeypatch, permission_resolver, seed_cluster
):
    """A database error's detail can quote the row it failed on, manifest
    text included. That string is shown to app.read callers as
    ``manifestBootstrapError`` and was logged with its traceback."""
    import logging

    from core.permissions import Permission

    org, app = _scaffold()
    seed_cluster(org)
    permission_resolver.grant(Permission.APP_CREATE)

    def failing_persist(app, manifest, *, raw_text=""):
        raise RuntimeError(f"Failing row contains ({raw_text})")

    monkeypatch.setattr("astrolift_manifest.persist.persist_manifest", failing_persist)

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("astrolift_registry.schema.mutations.registration")
    handler = _Capture(level=logging.DEBUG)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        result = _register(org, app.project, slug="failing-app", manifest_raw=_BASE_TOML)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert result.ok is True, result.errors
    registered = RegisteredApp.objects.get(slug="failing-app")
    assert registered.manifest_bootstrap_status == "parse_failed"
    assert _SECRET_VALUE not in registered.manifest_bootstrap_error
    formatted = "\n".join(logging.Formatter().format(record) for record in records)
    assert "manifest workload persist failed for failing-app" in formatted
    assert _SECRET_VALUE not in formatted


# ---- one reveal check per query (#1920 review) ---------------------


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_apps_list_checks_elevation_once_per_query(monkeypatch):
    """Every app on a list page asks the same elevation question; a
    query cannot change the answer mid-flight, so it is asked once."""
    from astrolift_identity import step_up
    from config.schema import schema
    from core.schema.context import StrawberryContext

    org, app = _scaffold()
    RegisteredApp.objects.create(
        organization=org,
        team=app.team,
        project=app.project,
        name="Second",
        slug="second-app",
        manifest_raw=_BASE_TOML,
    )
    viewer = _make_user("list-memo")
    _grant(viewer, org, "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    calls: list[int] = []
    real_check = step_up.is_elevated_and_attested

    def counting_check(info):
        calls.append(1)
        return real_check(info)

    monkeypatch.setattr("astrolift_identity.step_up.is_elevated_and_attested", counting_check)
    context = StrawberryContext(SimpleNamespace(user=viewer, session=session, META={}))

    with _ctx(org, viewer):
        result = schema.execute_sync("query { astroliftMyApps { slug rawManifest } }", context_value=context)

    assert result.errors is None, result.errors
    assert len(result.data["astroliftMyApps"]) == 2
    assert all(_SECRET_VALUE in item["rawManifest"] for item in result.data["astroliftMyApps"])
    assert len(calls) == 1


# ---- updateManifest identity check must not become a guess oracle (#1944 review) ----


def test_update_manifest_does_not_confirm_a_guessed_secret_via_staged_clearing(seed_cluster):
    """A caller without secret.read must not learn whether a guessed
    [env] value is correct from whether the staging buffer clears.

    The old check compared the *restored* submission (real [env]
    literals, put back by resolve_masked_env_values) against
    manifest_raw: true exactly when a guess happened to match the
    stored value, even though the response itself is masked either
    way -- an oracle a caller could use to confirm a secret one guess
    at a time without ever being shown it."""
    from astrolift_manifest.env_edit import redact_env_values

    org, app = _scaffold()
    seed_cluster(org)
    caller = _make_user("guess-oracle")
    _grant(caller, org, "app.update")

    masked = redact_env_values(_BASE_TOML)
    assert masked.count(REDACTED_ENV_VALUE) == 2  # API_KEY, LOG_LEVEL
    correct_guess = masked.replace(REDACTED_ENV_VALUE, _SECRET_VALUE, 1)  # API_KEY comes first

    result = _save(org, app, caller, correct_guess)

    assert result.ok is True, result.errors
    app.refresh_from_db()
    assert app.manifest_raw_staged != "", "a correct guess must not clear the staging buffer"


def test_update_manifest_still_clears_staged_for_an_unchanged_masked_resubmission(seed_cluster):
    """The oracle fix above must not break the legitimate case: a
    non-revealing caller who resubmits their own masked view of the
    synced manifest completely unchanged still clears staged, same as
    before."""
    from astrolift_manifest.env_edit import redact_env_values

    org, app = _scaffold()
    seed_cluster(org)
    _stage(app, _BASE_TOML.replace('LOG_LEVEL = "info"', 'LOG_LEVEL = "debug"'))
    caller = _make_user("unchanged-resubmit")
    _grant(caller, org, "app.update")

    result = _save(org, app, caller, redact_env_values(_BASE_TOML))

    assert result.ok is True, result.errors
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""
