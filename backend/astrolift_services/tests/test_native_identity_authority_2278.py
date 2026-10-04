"""Actual HTTP credential capture and committed PostgreSQL withdrawal checks."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Event
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import close_old_connections
from django.utils import timezone

from astrolift_identity import abac
from astrolift_identity.api_tokens import get_current_api_token, set_current_api_token
from astrolift_identity.models import AstroliftSession, Member, Policy, RoleBinding
from astrolift_identity.step_up_sso import SESSION_SSO_AUTH_TIME_KEY
from astrolift_services.native_identity_authority import (
    AcceptedAppIdentityAuthority,
    capture_app_identity_authority,
    current_app_identity_authority,
)
from astrolift_services.tests.test_cluster_model_mutations_2213 import subscription
from astrolift_services.tests.test_model_connection_2270 import (
    graphql_http,
    http_token,
)
from astrolift_services.tests.test_model_connection_2270 import world as foundation_world
from astrolift_services.tests.test_model_host_session_2269 import public
from astrolift_workflows.client import WorkflowHandle
from core.permissions import Permission, PermissionDenied
from core.tenancy import get_current_tenant

pytestmark = pytest.mark.django_db(transaction=True)
QUERY = "mutation($input:SubscribeClusterModelInput!){subscribeClusterModel(input:$input){ok errors{code}}}"


@pytest.fixture
def world(monkeypatch):
    return foundation_world.__wrapped__(monkeypatch)


def capture(world, client, monkeypatch, kind="api_token", *, before_capture=None, expect_refusal=False):
    refs = []
    if kind == "api_token":
        world.token, headers = http_token(world, scopes=("read:apps", "write:apps"))
        world.private_key = headers["HTTP_AUTHORIZATION"]
    else:
        client.force_login(world.user)
        headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
        graphql_http(client, headers, "query{__typename}", {})
        world.private_key = client.session.session_key
        world.sidecar = AstroliftSession.objects.get(session_key=world.private_key)

    def start(name, args, *, workflow_id):
        request = abac.current_attributes().request
        if before_capture:
            before_capture(request)
        try:
            refs.append(
                capture_app_identity_authority(
                    request, environment_guid=world.env.guid, permission=Permission.APP_UPDATE
                )
            )
        except PermissionDenied as error:
            if not expect_refusal:
                raise
            refs.append(error)
        return WorkflowHandle(workflow_id, "owned-test-run", True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)
    result = graphql_http(client, headers, QUERY, {"input": public(asdict(subscription(world)))})
    assert not result.get("errors"), result
    assert result["data"]["subscribeClusterModel"]["ok"], result
    assert len(refs) == 1
    assert isinstance(refs[0], PermissionDenied) == expect_refusal
    return refs[0]


@pytest.mark.parametrize("change", ["context_missing", "context_other", "hash", "team", "ceiling"])
def test_capture_binds_actual_authenticated_request_and_accepted_ceiling(world, client, monkeypatch, change):
    def change_after_authentication(request):
        if change == "context_missing":
            set_current_api_token(None)
        elif change == "context_other":
            other, _ = http_token(world, scopes=("read:apps", "write:apps"))
            set_current_api_token(other)
        elif change == "ceiling":
            request._api_token.scopes = ["read:apps"]
        else:
            fields = (
                {"token_hash": "changed-after-authentication"} if change == "hash" else {"team": world.medops}
            )
            type(world.token).objects.filter(pk=world.token.pk).update(**fields)

    capture(world, client, monkeypatch, before_capture=change_after_authentication, expect_refusal=True)
    assert get_current_tenant() is None and get_current_api_token() is None


def test_later_token_scope_expansion_does_not_expand_authenticated_reference(world, client, monkeypatch):
    def expand_after_authentication(request):
        type(world.token).objects.filter(pk=world.token.pk).update(
            scopes=["read:apps", "write:apps", "admin"]
        )

    ref = capture(world, client, monkeypatch, before_capture=expand_after_authentication)
    assert ref.accepted_token_scopes == ("read:apps", "write:apps")
    with current_app_identity_authority(ref):
        pass


def test_boolean_schema_is_not_a_supported_reference_version(world, client, monkeypatch):
    ref = capture(world, client, monkeypatch)
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(replace(ref, schema=True)):
            pytest.fail("boolean schema admitted")


@pytest.mark.parametrize("kind", ["api_token", "browser_session"])
def test_actual_http_captures_metadata_only_and_worker_rechecks_without_request(
    world, client, monkeypatch, kind
):
    ref = capture(world, client, monkeypatch, kind)
    payload = json.dumps(asdict(ref))
    assert world.private_key not in payload + repr(ref)
    assert "session_key" not in payload and "token_hash" not in payload and "password" not in payload
    assert get_current_tenant() is None and get_current_api_token() is None
    with current_app_identity_authority(ref) as env:
        assert env.pk == world.env.pk
        assert get_current_tenant().actor_user_id == world.user.pk
        assert abac.current_attributes().request is None
        assert abac.current_attributes().client_ip is None
    assert get_current_tenant() is None and get_current_api_token() is None


@pytest.mark.parametrize("kind", ["api_token", "browser_session"])
@pytest.mark.parametrize(
    "withdrawal",
    [
        "actor",
        "member",
        "role",
        "org",
        "team",
        "project",
        "app",
        "environment",
        "cluster",
        "provider",
        "retarget",
    ],
)
def test_original_owner_and_current_grants_are_not_cached(world, client, monkeypatch, kind, withdrawal):
    ref = capture(world, client, monkeypatch, kind)
    now = timezone.now()
    if withdrawal == "actor":
        type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
    elif withdrawal == "member":
        Member.all_objects.filter(user=world.user).update(deleted_at=now)
    elif withdrawal == "role":
        RoleBinding.all_objects.filter(user=world.user).update(deleted_at=now)
    elif withdrawal == "retarget":
        type(world.env).objects.filter(pk=world.env.pk).update(registered_app=world.platform_app)
    else:
        row = {
            "org": world.org,
            "team": world.medops,
            "project": world.medops_project,
            "app": world.medops_app,
            "environment": world.env,
            "cluster": world.cluster,
            "provider": world.cluster.provider_plugin,
        }[withdrawal]
        type(row).all_objects.filter(pk=row.pk).update(deleted_at=now)
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(ref):
            pytest.fail("withdrawn authority admitted")
    assert get_current_tenant() is None and get_current_api_token() is None


@pytest.mark.parametrize("withdrawal", ["revoke", "expire", "scope", "team", "token_hash", "delete"])
def test_original_bearer_is_bound_and_current_ceiling_is_enforced(world, client, monkeypatch, withdrawal):
    ref = capture(world, client, monkeypatch)
    changes = {
        "revoke": {"is_revoked": True},
        "expire": {"expires_at": timezone.now()},
        "scope": {"scopes": ["read:apps"]},
        "team": {"team": world.platform},
        "token_hash": {"token_hash": "changed-credential"},
        "delete": {"deleted_at": timezone.now()},
    }
    type(world.token).all_objects.filter(pk=world.token.pk).update(**changes[withdrawal])
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(ref):
            pytest.fail("withdrawn bearer admitted")


@pytest.mark.parametrize(
    "withdrawal",
    ["logout", "expire", "sidecar_revoke", "sidecar_delete", "sidecar_expire", "sidecar_rebind", "password"],
)
def test_original_browser_session_auth_hash_and_sidecar_are_rechecked(world, client, monkeypatch, withdrawal):
    ref = capture(world, client, monkeypatch, "browser_session")
    now = timezone.now()
    if withdrawal == "logout":
        Session.objects.filter(session_key=world.private_key).delete()
    elif withdrawal == "expire":
        Session.objects.filter(session_key=world.private_key).update(expire_date=now)
    elif withdrawal == "password":
        world.user.set_password("changed-after-dispatch")
        world.user.save()
    else:
        changes = {
            "sidecar_revoke": {"revoked_at": now},
            "sidecar_delete": {"deleted_at": now},
            "sidecar_expire": {"expires_at": now},
            "sidecar_rebind": {"session_key": "new-unrelated-key"},
        }
        AstroliftSession.all_objects.filter(pk=world.sidecar.pk).update(**changes[withdrawal])
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(ref):
            pytest.fail("withdrawn browser admitted")


@pytest.mark.parametrize("kind", ["api_token", "browser_session"])
@pytest.mark.parametrize(
    "field", ["app_guid", "environment_guid", "permission", "credential_guid", "actor_user_id", "signature"]
)
def test_reference_fields_cannot_be_substituted_with_known_public_metadata(
    world, client, monkeypatch, kind, field
):
    ref = capture(world, client, monkeypatch, kind)
    value = {
        "app_guid": str(world.platform_app.guid),
        "environment_guid": str(uuid4()),
        "permission": Permission.APP_DEPLOY.value,
        "credential_guid": str(uuid4()),
        "actor_user_id": world.user.pk + 1,
        "signature": "0" * 64,
    }[field]
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(replace(ref, **{field: value})):
            pytest.fail("substituted authority admitted")


def test_fresh_policy_and_browser_signin_time_are_read_from_actual_persisted_session(
    world, client, monkeypatch
):
    ref = capture(world, client, monkeypatch, "browser_session")
    store = SessionStore(world.private_key)
    store[SESSION_SSO_AUTH_TIME_KEY] = int(timezone.now().timestamp())
    store.save()
    Policy.objects.create(
        organization=world.org,
        name="freshness",
        slug="native-authority-freshness",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.update",
        conditions=[{"kind": "freshness", "max_session_age_minutes": 1}],
    )
    with current_app_identity_authority(ref):
        pass
    store[SESSION_SSO_AUTH_TIME_KEY] -= 600
    store.save()
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(ref):
            pytest.fail("stale signin admitted")


@pytest.mark.parametrize("kind", ["api_token", "browser_session"])
def test_background_check_does_not_invent_original_client_ip(world, client, monkeypatch, kind):
    ref = capture(world, client, monkeypatch, kind)
    Policy.objects.create(
        organization=world.org,
        name="network",
        slug="native-authority-network",
        scope_level="ORG",
        effect="DENY",
        action_pattern="app.update",
        conditions=[{"kind": "ip_allowlist", "cidrs": ["127.0.0.0/8"]}],
    )
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(ref):
            pytest.fail("invented network authority admitted")


def test_accepted_reference_survives_configured_signing_key_rotation(world, client, monkeypatch, settings):
    ref = capture(world, client, monkeypatch)
    old = settings.SECRET_KEY
    settings.SECRET_KEY = "different-active-application-secret"
    settings.SECRET_KEY_FALLBACKS = [old]
    with current_app_identity_authority(ref):
        pass
    settings.SECRET_KEY_FALLBACKS = []
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(ref):
            pytest.fail("retired reference key admitted")


@pytest.mark.parametrize("kind", ["api_token", "browser_session"])
def test_actual_temporal_converter_preserves_signed_reference_without_credentials(
    world, client, monkeypatch, kind
):
    from temporalio.converter import DataConverter

    ref = capture(world, client, monkeypatch, kind)

    async def roundtrip():
        converter = DataConverter.default
        payloads = await converter.encode([ref])
        assert world.private_key.encode() not in payloads[0].data
        restored = await converter.decode(payloads, [AcceptedAppIdentityAuthority])
        return restored[0]

    restored = asyncio.run(roundtrip())
    assert restored == ref
    with current_app_identity_authority(restored):
        pass


@pytest.mark.parametrize("kind", ["api_token", "browser_session"])
def test_separate_worker_connection_refuses_withdrawal_committed_while_waiting(
    world, client, monkeypatch, kind
):
    ref = capture(world, client, monkeypatch, kind)
    entered, resume = Event(), Event()

    def worker():
        close_old_connections()
        try:
            with current_app_identity_authority(ref):
                pass
            entered.set()
            assert resume.wait(10)
            with pytest.raises(PermissionDenied):
                with current_app_identity_authority(ref):
                    pytest.fail("committed withdrawal missed after worker wait")
            assert get_current_tenant() is None and get_current_api_token() is None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(worker)
        try:
            assert entered.wait(10)
            if kind == "api_token":
                type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
            else:
                AstroliftSession.all_objects.filter(pk=world.sidecar.pk).update(revoked_at=timezone.now())
        finally:
            resume.set()
        task.result(timeout=10)
