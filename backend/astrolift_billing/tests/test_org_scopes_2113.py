"""Organization billing cannot borrow selected team grants or bearer ceilings."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client
from django.utils import timezone

from astrolift_billing.models import Budget, CostSnapshot, Quota, QuotaIncreaseRequest, QuotaUsageSnapshot
from astrolift_billing.schema.mutations import BillingMutation, RequestQuotaIncreaseInput
from astrolift_billing.schema.queries import BillingQuery
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization
from astrolift_operations.models import Notification
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

READ_ROUTES = (
    "astrolift_quotas",
    "astrolift_quota_usage_history",
    "astrolift_budgets",
    "astrolift_cost_snapshots",
    "astrolift_cost_trend",
    "astrolift_cost_forecast",
    "astrolift_cost_by_binding",
)
ROUTES = (*READ_ROUTES, "request_quota_increase")


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    world = ScopeWorld("billing-2113")
    world.user = make_user("billing-2113")
    world.other_org = Organization.objects.create(name="Other", slug="other-billing-2113")
    world.quotas = [
        _quota(world.org, "TEAM", team.pk, used) for team, used in ((world.medops, 3), (world.platform, 7))
    ]
    world.foreign_quota = _quota(world.other_org, "ORG", world.other_org.pk, 999)
    world.budgets = [
        Budget.objects.create(
            organization=world.org, scope_kind="TEAM", scope_id=team.pk, amount_cents=amount
        )
        for team, amount in ((world.medops, 100), (world.platform, 200))
    ]
    Budget.objects.create(
        organization=world.other_org, scope_kind="ORG", scope_id=world.other_org.pk, amount_cents=999
    )
    world.costs = [
        CostSnapshot.objects.create(
            organization=world.org,
            registered_app=app,
            project=app.project,
            taken_at=timezone.localdate(),
            by="workload",
            amount_cents=amount,
            source="platform_meter",
        )
        for app, amount in ((world.medops_app, 100), (world.platform_app, 200))
    ]
    CostSnapshot.objects.create(
        organization=world.other_org,
        taken_at=timezone.localdate(),
        by="workload",
        amount_cents=999,
        source="platform_meter",
    )
    return world


def _quota(org, kind, scope_id, used):
    quota = Quota.objects.create(
        organization=org,
        scope_kind=kind,
        scope_id=scope_id,
        resource="apps",
        hard_limit=10,
        soft_limit=8,
        current_usage=used,
    )
    QuotaUsageSnapshot.objects.create(
        organization=org, quota=quota, captured_at=timezone.localdate(), used=used, limit=10
    )
    return quota


def _tenant(world, selected="own", *, actor=True, org=None):
    team = world.medops if selected == "own" else world.platform
    project = world.medops_project if selected == "own" else world.platform_project
    return tenant_context(
        TenantContext(
            organization_id=(org or world.org).pk,
            team_id=team.pk,
            project_id=project.pk,
            actor_user_id=world.user.pk if actor else None,
        )
    )


def _grant(world, kind="ORG", *, foreign=False):
    bind_role(
        world.user,
        permissions=[Permission.BILLING_READ],
        kind=kind,
        scope_id=world.other_org.pk
        if foreign
        else {"ORG": world.org.pk, "TEAM": world.medops.pk, "PROJECT": world.medops_project.pk}[kind],
        slug=f"billing-{uuid4().hex}",
    )


@contextmanager
def _token(world, *, team=None, org=None, scopes=None):
    token = ApiToken.objects.create(
        user=world.user,
        organization=org or world.org,
        team=team,
        name="billing 2113",
        token_hash=uuid4().hex,
        scopes=["admin"] if scopes is None else scopes,
    )
    handle = set_current_api_token(token)
    try:
        yield token
    finally:
        reset_current_api_token(handle)


def _invoke(world, name, quota=None):
    quota = quota or world.quotas[0]
    info = make_info(world.user)
    if name == "request_quota_increase":
        return BillingMutation().request_quota_increase(
            info, input=RequestQuotaIncreaseInput(quota_id=str(quota.guid), factor=2, reason="More apps")
        )
    kwargs = {"quota_id": str(quota.guid)} if name == "astrolift_quota_usage_history" else {}
    return getattr(BillingQuery(), name)(info, **kwargs)


def _denied(world, name, quota=None):
    if name == "request_quota_increase":
        result = _invoke(world, name, quota)
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    else:
        with pytest.raises(PermissionDenied):
            _invoke(world, name, quota)
    assert not QuotaIncreaseRequest.objects.exists()
    assert not Notification.objects.exists()
    assert not mail.outbox


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.parametrize(
    "kind,selected,foreign",
    [
        ("TEAM", "own", False),
        ("TEAM", "sibling", False),
        ("PROJECT", "own", False),
        ("ORG", "own", True),
    ],
)
def test_selected_team_project_and_foreign_org_grants_cannot_reach_billing(
    world, name, kind, selected, foreign
):
    _grant(world, kind, foreign=foreign)
    with _tenant(world, selected):
        _denied(world, name)


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.parametrize("selected", ["own", "sibling"])
def test_organization_grant_reaches_org_billing_with_either_team_selected(world, name, selected):
    _grant(world)
    with _tenant(world, selected):
        result = _invoke(world, name)
    if name == "astrolift_quotas":
        assert {str(row.id) for row in result} == {str(quota.guid) for quota in world.quotas}
    elif name == "astrolift_quota_usage_history":
        assert [row.used for row in result] == [3]
    elif name == "astrolift_budgets":
        assert {str(row.id) for row in result} == {str(budget.guid) for budget in world.budgets}
    elif name == "astrolift_cost_snapshots":
        assert {str(row.id) for row in result} == {str(cost.guid) for cost in world.costs}
    elif name == "astrolift_cost_trend":
        assert sum(row.amount_cents for row in result) == 300
    elif name == "astrolift_cost_forecast":
        assert result.mtd_cents == 300
    elif name == "astrolift_cost_by_binding":
        assert result.total_cents == result.unattributed_cents == 300
    else:
        assert result.ok, result.errors
        assert QuotaIncreaseRequest.objects.get().requested_by == world.user


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.parametrize("ceiling", ["team", "operator-team", "foreign-org", "read-apps"])
def test_bearer_cannot_widen_its_ceiling_through_owner_org_grant(world, name, ceiling):
    _grant(world)
    if ceiling == "operator-team":
        world.user.is_superuser = True
        world.user.save(update_fields=["is_superuser"])
    with (
        _tenant(world, "sibling"),
        _token(
            world,
            team=world.medops if ceiling in {"team", "operator-team"} else None,
            org=world.other_org if ceiling == "foreign-org" else None,
            scopes=["read:apps"] if ceiling == "read-apps" else None,
        ),
    ):
        _denied(world, name)


@pytest.mark.parametrize("name", ROUTES)
def test_organization_admin_bearer_preserves_allowed_billing_behavior(world, name):
    _grant(world)
    with _tenant(world), _token(world):
        result = _invoke(world, name)
    if name == "request_quota_increase":
        assert result.ok, result.errors
    else:
        assert result


@pytest.mark.parametrize("name", ["astrolift_quotas", "request_quota_increase"])
def test_selected_team_and_org_grant_without_actor_are_denied(world, name):
    _grant(world)
    with _tenant(world, actor=False):
        _denied(world, name)


@pytest.mark.parametrize("name", ["astrolift_quota_usage_history", "request_quota_increase"])
@pytest.mark.parametrize("target", ["foreign", "deleted", "missing"])
def test_quota_misses_cannot_fall_back_to_selected_team_authority(world, name, target):
    _grant(world, "TEAM")
    quota = world.foreign_quota if target == "foreign" else world.quotas[0]
    if target == "deleted":
        quota.soft_delete()
    elif target == "missing":
        quota.guid = uuid4()
    with _tenant(world):
        _denied(world, name, quota)


@pytest.mark.parametrize("name", ["astrolift_quotas", "request_quota_increase"])
def test_organization_role_cannot_be_reused_when_selecting_another_organization(world, name):
    _grant(world)
    with _tenant(world, org=world.other_org):
        _denied(world, name, world.foreign_quota)


def test_foreign_deleted_and_missing_quotas_keep_non_disclosing_behavior(world):
    _grant(world)
    deleted = _quota(world.org, "PROJECT", world.medops_project.pk, 5)
    deleted.soft_delete()
    with _tenant(world):
        for quota in (world.foreign_quota, deleted):
            assert _invoke(world, "astrolift_quota_usage_history", quota) == []
            result = _invoke(world, "request_quota_increase", quota)
            assert not result.ok and result.errors[0].code == "NOT_FOUND"
        assert (
            BillingQuery().astrolift_quota_usage_history(make_info(world.user), quota_id=str(uuid4())) == []
        )
        result = BillingMutation().request_quota_increase(
            make_info(world.user),
            input=RequestQuotaIncreaseInput(quota_id=str(uuid4()), factor=2, reason="x"),
        )
        assert not result.ok and result.errors[0].code == "NOT_FOUND"
    assert not QuotaIncreaseRequest.objects.exists()
    assert not Notification.objects.exists()


@pytest.mark.parametrize(
    "credential", ["team-session", "org-session", "team-token", "org-token", "read-token"]
)
@pytest.mark.parametrize("mutation", [False, True])
def test_http_billing_scope_with_selected_sibling_team(world, credential, mutation):
    _grant(world, "TEAM" if credential == "team-session" else "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_X_ASTROLIFT_TEAM": str(world.platform.pk),
    }
    if credential.endswith("session"):
        client.force_login(world.user)
        headers["HTTP_X_PLATFORM"] = "WEB"
    else:
        issued = mint_token()
        ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.medops if credential == "team-token" else None,
            name="http billing",
            token_hash=issued.token_hash,
            scopes=["read:apps"] if credential == "read-token" else ["admin"],
        )
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    if mutation:
        data = {
            "query": "mutation($input: RequestQuotaIncreaseInput!) { requestQuotaIncrease(input: $input) { ok errors { code } } }",
            "variables": {"input": {"quotaId": str(world.quotas[0].guid), "factor": 2, "reason": "x"}},
        }
    else:
        data = {
            "query": "{ astroliftQuotas { id } astroliftBudgets { id } astroliftCostForecast { mtdCents } }"
        }
    response = client.post(
        f"/{settings.BASE_URL}gql/config/", data=data, content_type="application/json", **headers
    )
    assert response.status_code == 200, response.content
    payload = response.json()
    allowed = credential in {"org-session", "org-token"}
    if mutation:
        assert not payload.get("errors"), payload
        result = payload["data"]["requestQuotaIncrease"]
        assert result["ok"] is allowed
        assert QuotaIncreaseRequest.objects.count() == int(allowed)
        if not allowed:
            assert result["errors"] == [{"code": "PERMISSION_DENIED"}]
            assert not Notification.objects.exists()
            assert not mail.outbox
    elif allowed:
        assert not payload.get("errors"), payload
        assert {row["id"] for row in payload["data"]["astroliftQuotas"]} == {
            str(quota.guid) for quota in world.quotas
        }
        assert payload["data"]["astroliftCostForecast"]["mtdCents"] == 300
    else:
        assert payload.get("errors") and payload.get("data") is None, payload
