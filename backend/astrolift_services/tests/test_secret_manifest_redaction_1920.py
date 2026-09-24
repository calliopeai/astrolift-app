"""#1920: the secret-write mutations echo the staged manifest back to
the caller. That response must mask every ``[env]`` value unless the
caller holds ``secret.read`` and is step-up elevated (the same gate
``revealAppSecret`` enforces), even though these mutations themselves
only require ``app.update``. Without the fix, an ``app.update``-only
caller writing one key could read every other secret already staged
on the app through the response payload.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_identity.session_elevation import METHOD_PASSWORD, elevate
from astrolift_manifest.env_edit import REDACTED_ENV_VALUE
from astrolift_registry.models import RegisteredApp
from astrolift_services.schema.mutations import (
    BulkImportAppSecretsInput,
    DeleteAppSecretInput,
    RotateAppSecretInput,
    ServicesMutation,
    SetAppSecretInput,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_SECRET_VALUE = "sk-live-super-secret-456"
_OTHER_KEY = "OTHER_SECRET"

_BASE_TOML = f"""\
astrolift_version = 1
name = "hello"

[env]
{_OTHER_KEY} = "{_SECRET_VALUE}"

[[workloads]]
name = "web"
kind = "deployment"
"""


class _FakeSession(dict):
    modified = False


def _info(user, *, session: dict | None = None):
    session = session if session is not None else _FakeSession()
    request = SimpleNamespace(user=user, session=session, META={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _make_user(username: str):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _grant(user, org, *permissions: str) -> None:
    """Real Role + org-scoped RoleBinding. ``can_reveal_app_secrets``'s
    single-app fallback reads ``RoleBinding`` rows directly via
    ``resolve_effective_permissions``; the ``permission_resolver``
    stub fixture other tests in this module use is invisible to it."""
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


# ---- setAppSecret ---------------------------------------------------


def test_set_app_secret_masks_other_secrets_for_app_update_only():
    org, app = _scaffold()
    caller = _make_user("set-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().set_app_secret(
            _info(caller),
            input=SetAppSecretInput(app_slug=app.slug, key="NEW_KEY", value="new-value"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert _OTHER_KEY in result.data.raw_manifest_staged
    assert REDACTED_ENV_VALUE in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_set_app_secret_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("set-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().set_app_secret(
            _info(caller, session=session),
            input=SetAppSecretInput(app_slug=app.slug, key="NEW_KEY", value="new-value"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE in result.data.raw_manifest_staged


# ---- rotateAppSecret --------------------------------------------------


def test_rotate_app_secret_masks_other_secrets_for_app_update_only():
    org, app = _scaffold()
    caller = _make_user("rotate-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().rotate_app_secret(
            _info(caller),
            input=RotateAppSecretInput(app_slug=app.slug, key=_OTHER_KEY, value="rotated-value"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert "rotated-value" not in result.data.raw_manifest_staged
    assert _OTHER_KEY in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_rotate_app_secret_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("rotate-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().rotate_app_secret(
            _info(caller, session=session),
            input=RotateAppSecretInput(app_slug=app.slug, key=_OTHER_KEY, value="rotated-value"),
        )

    assert result.ok is True, result.errors
    assert "rotated-value" in result.data.raw_manifest_staged


# ---- deleteAppSecret ----------------------------------------------


def test_delete_app_secret_masks_remaining_secrets_for_app_update_only():
    org, app = _scaffold()
    app.manifest_raw = _BASE_TOML.replace(
        "[[workloads]]",
        'KEEP_SECRET = "keep-me-secret"\n\n[[workloads]]',
    )
    app.save(update_fields=["manifest_raw"])
    caller = _make_user("delete-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().delete_app_secret(
            _info(caller),
            input=DeleteAppSecretInput(app_slug=app.slug, key=_OTHER_KEY),
        )

    assert result.ok is True, result.errors
    assert "keep-me-secret" not in result.data.raw_manifest_staged
    assert "KEEP_SECRET" in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_delete_app_secret_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    app.manifest_raw = _BASE_TOML.replace(
        "[[workloads]]",
        'KEEP_SECRET = "keep-me-secret"\n\n[[workloads]]',
    )
    app.save(update_fields=["manifest_raw"])
    caller = _make_user("delete-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().delete_app_secret(
            _info(caller, session=session),
            input=DeleteAppSecretInput(app_slug=app.slug, key=_OTHER_KEY),
        )

    assert result.ok is True, result.errors
    assert "keep-me-secret" in result.data.raw_manifest_staged


# ---- bulkImportAppSecrets ------------------------------------------


def test_bulk_import_app_secrets_masks_existing_secrets_for_app_update_only():
    org, app = _scaffold()
    caller = _make_user("bulk-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(caller),
            input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text="IMPORTED_KEY=imported-value\n"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert "imported-value" not in result.data.raw_manifest_staged
    assert _OTHER_KEY in result.data.raw_manifest_staged
    assert "IMPORTED_KEY" in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_bulk_import_app_secrets_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("bulk-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(caller, session=session),
            input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text="IMPORTED_KEY=imported-value\n"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE in result.data.raw_manifest_staged
    assert "imported-value" in result.data.raw_manifest_staged


# ---- secret change proposals (#1920 review) -------------------------

_PROPOSED_VALUE = "sk-live-proposed-789"


def _proposal(app, proposer, *, op: str = "set", payload: dict | None = None):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_services.models import SecretChangeProposal
    from astrolift_services.secret_change_diff import build_diff

    payload = payload if payload is not None else {"key": _OTHER_KEY, "value": _PROPOSED_VALUE}
    return SecretChangeProposal.objects.create(
        registered_app=app,
        proposer=proposer,
        op=op,
        payload=payload,
        payload_diff=build_diff(app=app, op=op, payload=payload, environment_name=""),
        expires_at=timezone.now() + timedelta(days=7),
        created_by=proposer,
        updated_by=proposer,
    )


def _read_proposal(org, viewer, proposal, *, session=None):
    from astrolift_graphql import GUID
    from astrolift_services.schema.queries import ServicesQuery

    with _ctx(org, viewer):
        return ServicesQuery().astrolift_secret_change_proposal(
            _info(viewer, session=session), id=GUID(str(proposal.guid))
        )


def test_proposal_diff_hints_collapse_for_a_viewer_who_cannot_reveal():
    """``value_masked`` is the first three and last characters of the
    current and proposed values: four plaintext characters of a secret
    for anyone with app.read."""
    org, app = _scaffold()
    viewer = _make_user("proposal-app-read")
    _grant(viewer, org, "app.read")
    proposal = _proposal(app, viewer)
    assert proposal.payload_diff["after"]["value_masked"] == "sk-***9"

    result = _read_proposal(org, viewer, proposal)

    assert result.payload == {"key": _OTHER_KEY, "value": "[REDACTED]"}
    assert result.payload_diff["before"]["value_masked"] == "***"
    assert result.payload_diff["after"]["value_masked"] == "***"
    assert result.payload_diff["after"]["key"] == _OTHER_KEY


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_proposal_diff_hints_stay_for_a_viewer_who_can_reveal():
    org, app = _scaffold()
    viewer = _make_user("proposal-secret-read")
    _grant(viewer, org, "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)
    proposal = _proposal(app, viewer)

    result = _read_proposal(org, viewer, proposal, session=session)

    assert result.payload["value"] == _PROPOSED_VALUE
    assert result.payload_diff["after"]["value_masked"] == "sk-***9"
    assert result.payload_diff["before"]["value_masked"] == "sk-***6"


def test_proposals_list_asks_the_reveal_question_once_per_app(monkeypatch):
    """Every proposal on the list page belongs to the same app, and a
    bundle proposal carries nothing to mask at all."""
    from astrolift_identity import permission_resolver
    from config.schema import schema
    from core.schema.context import StrawberryContext

    org, app = _scaffold()
    viewer = _make_user("proposal-list")
    _grant(viewer, org, "app.read")
    for _ in range(3):
        _proposal(app, viewer)
    _proposal(app, viewer, op="attach_bundle", payload={"bundle_slug": "shared", "prefix": ""})

    lookups: list[object] = []
    real_resolve = permission_resolver.resolve_effective_permissions

    def counting_resolve(tenant, **kwargs):
        if kwargs.get("extra_scope") == ("APP", app.pk):
            lookups.append(kwargs)
        return real_resolve(tenant, **kwargs)

    monkeypatch.setattr(
        "astrolift_identity.permission_resolver.resolve_effective_permissions", counting_resolve
    )
    context = StrawberryContext(SimpleNamespace(user=viewer, session=_FakeSession(), META={}))

    with _ctx(org, viewer):
        result = schema.execute_sync(
            'query { astroliftSecretChangeProposals(appSlug: "hello-app") { op payload payloadDiff } }',
            context_value=context,
        )

    assert result.errors is None, result.errors
    rows = result.data["astroliftSecretChangeProposals"]
    assert len(rows) == 4
    assert all(_PROPOSED_VALUE not in str(row) for row in rows)
    assert len(lookups) == 1


def test_proposal_with_nothing_to_mask_skips_the_reveal_check(monkeypatch):
    from astrolift_services.schema.types import secret_change_proposal_to_type

    org, app = _scaffold()
    viewer = _make_user("proposal-bundle")
    _grant(viewer, org, "app.read")
    proposal = _proposal(app, viewer, op="attach_bundle", payload={"bundle_slug": "shared", "prefix": ""})

    def no_reveal_check(*args, **kwargs):
        raise AssertionError("nothing to mask, so nothing to ask")

    monkeypatch.setattr("astrolift_services.schema.types.can_reveal_app_secrets", no_reveal_check)
    with _ctx(org, viewer):
        result = secret_change_proposal_to_type(proposal, info=_info(viewer))

    assert result.payload == {"bundle_slug": "shared", "prefix": ""}


# ---- the masked placeholder is never a value (#1920 review) --------


def test_secret_writes_refuse_the_masked_placeholder_as_a_value():
    """Pasted from a masked read, the placeholder would become the stored
    secret and leave a manifest ``parse_raw`` refuses, blocking every
    later save and deploy of it."""
    from astrolift_services.schema.mutations import ProposeSecretChangeInput

    org, app = _scaffold()
    caller = _make_user("placeholder-writer")
    _grant(caller, org, "app.update")
    writes = [
        lambda: ServicesMutation().set_app_secret(
            _info(caller), input=SetAppSecretInput(app_slug=app.slug, key="NEW_KEY", value=REDACTED_ENV_VALUE)
        ),
        lambda: ServicesMutation().rotate_app_secret(
            _info(caller),
            input=RotateAppSecretInput(app_slug=app.slug, key=_OTHER_KEY, value=REDACTED_ENV_VALUE),
        ),
        lambda: ServicesMutation().bulk_import_app_secrets(
            _info(caller),
            input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text=f"NEW_KEY={REDACTED_ENV_VALUE}\n"),
        ),
        lambda: ServicesMutation().propose_secret_change(
            _info(caller),
            input=ProposeSecretChangeInput(
                app_slug=app.slug, op="set", key="NEW_KEY", value=REDACTED_ENV_VALUE
            ),
        ),
    ]

    with _ctx(org, caller):
        results = [write() for write in writes]

    for result in results:
        assert result.ok is False
        assert result.errors[0].code == "VALIDATION"
        assert "masked placeholder" in result.errors[0].message
    app.refresh_from_db()
    assert REDACTED_ENV_VALUE not in (app.manifest_raw_staged or "")
