"""The builder API follows the org's chat_studio_integration module (#1859).

Acceptance: org A enabled, org B not. A device-flow token for B sees
``enabled: false`` in ``me.modules`` and every builder route answers 403 with
a reason, while A's token gets through. Tokens come from the real device
flow and requests go through the full middleware stack; only Temporal is
patched, as in ``test_builder_api.py``.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity import device_flow
from astrolift_identity.models import Member, Organization, OrganizationModule, Role, RoleBinding, Team

pytestmark = pytest.mark.django_db

MODULE = "chat_studio_integration"
ROUTES = (
    ("post", "/api/builder/v1/dev-environments/"),
    ("put", "/api/builder/v1/dev-environments/{guid}/files/"),
    ("post", "/api/builder/v1/dev-environments/{guid}/promote/"),
)


@pytest.fixture(autouse=True)
def _no_temporal(monkeypatch):
    from astrolift_workflows.client import WorkflowHandle

    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, args, *, workflow_id, task_queue=None: WorkflowHandle(
            workflow_id=workflow_id, run_id="run-1", enqueued=True
        ),
    )


def _org(slug: str, *, enabled: bool) -> Organization:
    org = Organization.objects.create(name=slug, slug=slug)
    Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug}")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="k8s-gate",
        defaults={"name": "K8s", "plugin_version": "0.0.1", "capabilities_manifest": {}, "config_schema": {}},
    )
    TenantCluster.objects.create(
        organization=org,
        name=f"{slug}-cluster",
        slug=f"{slug}-cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://gate-cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        is_active=True,
    )
    if enabled:
        OrganizationModule.objects.create(organization=org, key=MODULE, enabled=True)
    return org


def _device_flow_bearer(org: Organization) -> str:
    """An ``alft_at_`` access token minted by the real CLI device flow for ``org``."""
    user = get_user_model().objects.create_user(
        username=f"cli-{org.slug}", email=f"cli-{org.slug}@astrolift.dev", password="pw"
    )
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    row, session_id = device_flow.create_session(client_label="chat-studio")
    assert device_flow.approve_session(row, user=user, organization=org) is None
    later = timezone.now() + device_flow.MIN_POLL_INTERVAL + dt.timedelta(seconds=1)
    result = device_flow.poll_complete(session_id, now=later)
    assert result.status == "issued"
    return result.credentials.access_token


def _call(method: str, path: str, bearer: str):
    body = {"runtime": "python", "files": {"main.py": "print(1)"}, "app_name": "Gate"}
    return getattr(Client(), method)(
        path.format(guid=uuid.uuid4()),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
    )


def _graphql(bearer: str, query: str, variables: dict | None = None) -> dict:
    response = Client().post(
        "/app/gql/config/",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
    )
    assert response.status_code == 200, response.content
    payload = response.json()
    assert "errors" not in payload, payload
    return payload["data"]


def _me_modules(bearer: str) -> dict[str, dict]:
    data = _graphql(bearer, "{ me { modules { key enabled canCreate } } }")
    return {row["key"]: row for row in data["me"]["modules"]}


_SET_MODULE = """
mutation Set($input: SetOrganizationModuleInput!) {
  setOrganizationModule(input: $input) {
    ok
    errors { code }
    data { key enabled }
  }
}
"""
_ENABLE = {"input": {"key": MODULE, "enabled": True}}


def test_acceptance_org_b_is_off_org_a_is_on():
    org_a = _org("gate-org-a", enabled=True)
    org_b = _org("gate-org-b", enabled=False)
    bearer_a = _device_flow_bearer(org_a)
    bearer_b = _device_flow_bearer(org_b)

    assert _me_modules(bearer_b)[MODULE]["enabled"] is False
    assert _me_modules(bearer_a)[MODULE]["enabled"] is True

    for method, path in ROUTES:
        denied = _call(method, path, bearer_b)
        assert denied.status_code == 403, (path, denied.content)
        assert denied.json() == {
            "detail": "the chat_studio_integration module is not enabled for this organization",
            "reason": "module_not_enabled",
            "module": MODULE,
        }

    # A's token passes the gate and reaches the view proper.
    created = _call("post", ROUTES[0][1], bearer_a)
    assert created.status_code == 201, created.content
    # Unknown dev environment: past the gate, so a plain 404.
    for method, path in ROUTES[1:]:
        assert _call(method, path, bearer_a).status_code == 404


def test_install_force_off_denies_an_enabled_org_with_its_own_reason():
    org_a = _org("gate-org-forced", enabled=True)
    bearer = _device_flow_bearer(org_a)
    with override_config(CHAT_STUDIO_INTEGRATION_ALLOWED=False):
        assert _me_modules(bearer)[MODULE]["enabled"] is False
        for method, path in ROUTES:
            denied = _call(method, path, bearer)
            assert denied.status_code == 403
            assert denied.json()["reason"] == "module_disabled_by_install"
    assert _call("post", ROUTES[0][1], bearer).status_code == 201


def test_gate_runs_before_the_dev_environment_lookup():
    """A disabled org cannot probe which dev environment guids exist."""
    org_a = _org("gate-org-owner", enabled=True)
    org_b = _org("gate-org-prober", enabled=False)
    owner = _device_flow_bearer(org_a)
    created = _call("post", ROUTES[0][1], owner)
    guid = created.json()["id"]
    prober = _device_flow_bearer(org_b)
    response = Client().put(
        f"/api/builder/v1/dev-environments/{guid}/files/",
        data=json.dumps({"files": {"a.py": "x"}}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {prober}",
    )
    assert response.status_code == 403
    assert response.json()["reason"] == "module_not_enabled"


def _admin_api_token(org: Organization, perms: tuple[str, ...]) -> str:
    """An admin-scoped API token (the settings surface a browser admin uses),
    for a member holding ``perms``. Device-flow CLI tokens cannot reach
    ``org.update``: their scope ceiling stops short of org settings."""
    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken

    user = get_user_model().objects.create_user(
        username=f"admin-{org.slug}", email=f"admin-{org.slug}@astrolift.dev", password="pw"
    )
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    role = Role.objects.create(
        name=f"admin-role-{org.slug}",
        slug=f"admin-role-{org.slug}",
        scope_level=Role.ScopeLevel.ORG,
        permissions=list(perms),
        is_system=False,
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)
    minted = mint_token()
    ApiToken.objects.create(
        user=user,
        organization=org,
        name="settings",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=["admin"],
    )
    return minted.plaintext


def test_org_admin_turns_the_module_on_over_graphql_and_the_builder_opens():
    """End to end with the real permission resolver: only a member holding
    ``org.update`` can switch the module on, and doing so opens the builder
    to the org's device-flow tokens."""
    org = _org("gate-org-admin", enabled=False)
    cli = _device_flow_bearer(org)
    assert _call("post", ROUTES[0][1], cli).status_code == 403

    member = _admin_api_token(org, perms=("org.read",))
    denied = _graphql(member, _SET_MODULE, _ENABLE)["setOrganizationModule"]
    assert denied["ok"] is False
    assert denied["errors"][0]["code"] == "PERMISSION_DENIED"
    assert _call("post", ROUTES[0][1], cli).status_code == 403

    admin_org = _org("gate-org-admin-2", enabled=False)
    admin = _admin_api_token(admin_org, perms=("org.read", "org.update"))
    result = _graphql(admin, _SET_MODULE, _ENABLE)["setOrganizationModule"]
    assert result == {"ok": True, "errors": [], "data": {"key": MODULE, "enabled": True}}
    admin_cli = _device_flow_bearer(admin_org)
    assert _me_modules(admin_cli)[MODULE]["enabled"] is True
    assert _call("post", ROUTES[0][1], admin_cli).status_code == 201
