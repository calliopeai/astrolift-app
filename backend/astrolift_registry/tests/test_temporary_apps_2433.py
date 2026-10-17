"""Temporary apps (#2433): an expiry on registerApp, hostAppOnBrain and the
Chat Studio promote, setAppExpiry, the organization's policy, the timer's
decisions, warnings, the expiry itself and its grace period, MCP, and the
durable timer across a worker restart."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from contextlib import contextmanager

import pytest
from django.utils import timezone

from astrolift_identity.api_tokens import (
    SCOPE_MCP_READ,
    SCOPE_MCP_WRITE,
    SCOPE_READ_APPS,
    SCOPE_WRITE_APPS,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ActionSafetyRule, ApiToken, Member
from astrolift_operations.models import Notification, UserAlertSubscription
from astrolift_registry import expiry
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from astrolift_registry.schema.mutations.expiry import SetAppExpiryInput, TemporaryAppPolicyInput
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.schema.types import AppsListFilterInput
from astrolift_services.models import ManagedService
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db

ALL = [
    Permission.APP_READ,
    Permission.APP_CREATE,
    Permission.APP_UPDATE,
    Permission.APP_DEPLOY,
    Permission.APP_DELETE,
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("astrolift_scm.services.autowire.run_autowire", lambda app, actor=None: None)

    def start(name, args, *, workflow_id, task_queue=None, replace_running=True):
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id="r", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)


@pytest.fixture
def armed(monkeypatch):
    from types import SimpleNamespace

    from django.db import transaction

    calls: list[int] = []
    monkeypatch.setattr(expiry, "arm", lambda pk, guid: calls.append(pk))
    # The test's transaction never commits: run the arm as a commit would.
    monkeypatch.setattr(
        expiry, "transaction", SimpleNamespace(atomic=transaction.atomic, on_commit=lambda fn: fn())
    )
    return calls


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr("core.mutations._audit_writer", rows.append)
    return rows


def _world(suffix, permissions=ALL):
    from astrolift_clusters.models import TenantCluster

    world = ScopeWorld(suffix)
    world.user = make_user(suffix)
    world.cluster = make_cluster(world, suffix)
    TenantCluster.objects.filter(pk=world.cluster.pk).update(lifecycle=TenantCluster.Lifecycle.MANAGED.value)
    bind_role(world.user, permissions=permissions, kind="ORG", scope_id=world.org.pk, slug=f"r-{suffix}")
    return world


@pytest.fixture
def world():
    return _world("tmp2433")


@pytest.fixture
def other():
    return _world("tmpx2433")


@contextmanager
def _as(world, user=None, token=None):
    user = user or world.user
    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=user.pk)
    ):
        marker = set_current_api_token(token) if token is not None else None
        try:
            yield make_info(user)
        finally:
            if marker is not None:
                reset_current_api_token(marker)


def _register(world, user=None, token=None, **fields):
    data = {"project_id": str(world.medops_project.guid), "source_repo": "", "slug": "demo", **fields}
    with _as(world, user, token) as info:
        return RegistryMutation().register_app(info, input=RegisterAppInput(**data))


def _set(world, user=None, **fields):
    with _as(world, user) as info:
        return RegistryMutation().set_app_expiry(info, input=SetAppExpiryInput(**fields))


def _token(world, scopes, suffix="t"):
    plaintext = f"alft_at_tmp-{suffix}"
    return ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="tmp",
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        token_last_4="2433",
        scopes=scopes,
    )


def _temp_app(world, *, expires_at=None, slug="tmp-app", **fields):
    return RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops,
        project=world.medops_project,
        name="Temp",
        slug=slug,
        k8s_namespace=f"acme-{slug}",
        provisioning_status="ready",
        expires_at=expires_at or timezone.now() + dt.timedelta(days=3),
        **fields,
    )


# ---------------------------------------------------------------------------
# Durations and the policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        ("30m", dt.timedelta(minutes=30)),
        ("4h", dt.timedelta(hours=4)),
        ("7d", dt.timedelta(days=7)),
        ("6w", dt.timedelta(weeks=6)),
        ("3mo", dt.timedelta(days=90)),
    ],
)
def test_durations(text, delta):
    assert expiry.parse_duration(text) == delta


@pytest.mark.parametrize("text", ["", "7", "7y", "-1d", "1.5h", "d7"])
def test_bad_durations(text):
    with pytest.raises(expiry.ExpiryError):
        expiry.parse_duration(text)


def test_resolve_keeps_inside_minimum_and_policy(world):
    now = timezone.now()
    assert expiry.resolve(world.org, expires_in=None, expires_at=None) is None
    assert expiry.resolve(world.org, expires_in="1h", expires_at=None, now=now) == now + dt.timedelta(hours=1)
    with pytest.raises(expiry.ExpiryError, match="15 minutes"):
        expiry.resolve(world.org, expires_in="10m", expires_at=None)
    with pytest.raises(expiry.ExpiryError, match="at most 90d"):
        expiry.resolve(world.org, expires_in="91d", expires_at=None)
    with pytest.raises(expiry.ExpiryError, match="not both"):
        expiry.resolve(world.org, expires_in="1h", expires_at=now + dt.timedelta(hours=2))
    world.org.temporary_app_policy = {"max_ttl_seconds": 7200}
    with pytest.raises(expiry.ExpiryError, match="at most 2h"):
        expiry.resolve(world.org, expires_in="3h", expires_at=None)


def test_policy_mutation_validates_and_needs_org_update(world):
    with _as(world) as info:
        refused = RegistryMutation().set_temporary_app_policy(info, input=TemporaryAppPolicyInput())
    assert refused.ok is False and refused.errors[0].code == "PERMISSION_DENIED"

    bind_role(
        world.user, permissions=[Permission.ORG_UPDATE], kind="ORG", scope_id=world.org.pk, slug="t-org"
    )
    with _as(world) as info:
        bad = RegistryMutation().set_temporary_app_policy(
            info, input=TemporaryAppPolicyInput(ttl_required_surfaces=["fax"])
        )
        ok = RegistryMutation().set_temporary_app_policy(
            info,
            input=TemporaryAppPolicyInput(
                max_ttl="30d", ttl_required_surfaces=["mcp", "chat_studio"], data_grace_days=3
            ),
        )
        read = RegistryQuery().astrolift_temporary_app_policy(info)
    assert bad.errors[0].field == "ttlRequiredSurfaces"
    assert ok.ok, ok.errors
    assert read.max_ttl_seconds == 30 * 86400 and read.min_ttl_seconds == 900
    assert read.ttl_required_surfaces == ["chat_studio", "mcp"] and read.data_grace_days == 3


# ---------------------------------------------------------------------------
# Creating a temporary app
# ---------------------------------------------------------------------------


def test_register_with_expires_in_makes_a_temporary_app(world, armed, audit):
    before = timezone.now()
    result = _register(world, expires_in="4h", delete_data_on_expiry=True)
    assert result.ok, result.errors
    app = RegisteredApp.objects.get(organization=world.org, slug="demo")
    assert before + dt.timedelta(hours=4) <= app.expires_at <= timezone.now() + dt.timedelta(hours=4)
    assert app.delete_data_on_expiry is True
    assert app.created_by_id == world.user.pk
    assert result.data.expires_at == app.expires_at
    assert armed == [app.pk]
    rows = [a for a in audit if a.action == "app.expiry.set"]
    assert rows and rows[0].target_id == "demo" and rows[0].extra["change"] == "set"


def test_register_refuses_out_of_policy_expiries(world, armed):
    short = _register(world, expires_in="5m")
    assert short.ok is False and short.errors[0].field == "expiresIn"
    long = _register(world, expires_at=timezone.now() + dt.timedelta(days=120))
    assert long.ok is False and long.errors[0].field == "expiresAt"
    assert not RegisteredApp.objects.filter(organization=world.org, slug="demo").exists()
    assert armed == []


def test_manifest_expires_in_is_the_declarative_form(world, armed):
    manifest = (
        'name = "demo"\n\n[app]\nexpires_in = "7d"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\n\n'
        '[[workloads.containers]]\nname = "app"\nis_primary = true\nimage_ref = "nginx:1"\nport = 80\n'
    )
    result = _register(world, manifest_raw=manifest)
    assert result.ok, result.errors
    app = RegisteredApp.objects.get(organization=world.org, slug="demo")
    assert (
        dt.timedelta(days=7) - dt.timedelta(minutes=1)
        < app.expires_at - timezone.now()
        <= dt.timedelta(days=7)
    )
    assert app.manifest_normalized.get("app_expires_in") == "7d"
    # The input wins over the manifest.
    again = _register(world, slug="demo-2", manifest_raw=manifest, expires_in="1h")
    assert again.ok and again.data.expires_at - timezone.now() <= dt.timedelta(hours=1)


def test_policy_can_require_an_expiry_on_a_surface(world, armed):
    world.org.temporary_app_policy = {"ttl_required_surfaces": ["api_token"]}
    world.org.save(update_fields=["temporary_app_policy"])
    token = _token(world, [SCOPE_READ_APPS, SCOPE_WRITE_APPS])
    refused = _register(world, token=token)
    assert refused.ok is False and refused.errors[0].code == "PERMISSION_DENIED"
    assert "API tokens" in refused.errors[0].message
    assert _register(world, token=token, expires_in="1d").ok
    # The web console is not a required surface.
    assert _register(world, slug="from-web").ok


def test_members_can_be_held_to_temporary_apps(world, armed):
    world.org.temporary_app_policy = {"temporary_only_for_members": True}
    world.org.save(update_fields=["temporary_app_policy"])
    member = make_user("tmp2433-member")
    bind_role(member, permissions=ALL, kind="ORG", scope_id=world.org.pk, slug="tmp-member")
    refused = _register(world, user=member)
    assert refused.ok is False and "temporary apps only" in refused.errors[0].message
    assert _register(world, user=member, expires_in="2h").ok
    # Clearing is refused for the member too.
    cleared = _set(world, user=member, app_slug="demo", clear=True)
    assert cleared.ok is False and cleared.errors[0].field == "clear"
    # An org admin may still create a permanent app.
    bind_role(world.user, permissions=[Permission.ORG_UPDATE], kind="ORG", scope_id=world.org.pk, slug="adm")
    assert _register(world, slug="permanent").ok


def test_host_app_on_brain_takes_an_expiry(world, armed, monkeypatch):
    from astrolift_registry import brain_apps
    from astrolift_registry.schema.mutations.brains import HostAppOnBrainInput
    from astrolift_registry.tests.test_brain_management_2424 import _brain

    brain = _brain(world)
    monkeypatch.setattr(brain_apps, "mcp_urls", lambda app: ["https://kb.acme.test/mcp"])
    with _as(world) as info:
        result = RegistryMutation().host_app_on_brain(
            info,
            input=HostAppOnBrainInput(
                brain_slug=brain.slug,
                project_id=str(world.medops_project.guid),
                image="ghcr.io/acme/app:1",
                slug="on-brain",
                expires_in="1d",
            ),
        )
    assert result.ok, result.errors
    assert result.data.app.expires_at is not None
    assert RegisteredApp.objects.get(slug="on-brain").expires_at == result.data.app.expires_at


# ---------------------------------------------------------------------------
# setAppExpiry
# ---------------------------------------------------------------------------


def test_extend_shorten_and_clear(world, armed, audit):
    app = _temp_app(world, expiry_warnings_sent=["24h"])
    old = app.expires_at

    extended = _set(world, app_slug=app.slug, extend_by="2d")
    assert extended.ok, extended.errors
    app.refresh_from_db()
    assert app.expires_at == old + dt.timedelta(days=2)
    assert app.expiry_warnings_sent == []  # sent again for the new time

    shortened = _set(world, app_slug=app.slug, expires_in="1h")
    assert shortened.ok
    cleared = _set(world, app_slug=app.slug, clear=True)
    assert cleared.ok and cleared.data.expires_at is None

    actions = [(a.action, (a.extra or {}).get("change")) for a in audit if a.action.startswith("app.expiry.")]
    assert ("app.expiry.extend", "extend") in actions
    assert ("app.expiry.set", "shorten") in actions
    assert ("app.expiry.clear", "clear") in actions
    assert armed == [app.pk, app.pk, app.pk]


def test_set_expiry_needs_exactly_one_form_and_app_update(world, armed):
    app = _temp_app(world)
    both = _set(world, app_slug=app.slug, expires_in="1h", clear=True)
    assert both.ok is False and both.errors[0].code == "VALIDATION"
    reader = make_user("tmp2433-reader")
    bind_role(reader, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.pk, slug="tmp-reader")
    denied = _set(world, user=reader, app_slug=app.slug, extend_by="1d")
    assert denied.ok is False and denied.errors[0].code == "PERMISSION_DENIED"
    permanent = _temp_app(world, slug="perm")
    RegisteredApp.objects.filter(pk=permanent.pk).update(expires_at=None)
    assert _set(world, app_slug="perm", extend_by="1d").errors[0].code == "PRECONDITION"
    assert armed == []


def test_clear_asks_what_the_org_requires_for_permanent_apps(world, armed, monkeypatch):
    import astrolift_identity.action_safety as safety

    app = _temp_app(world)
    monkeypatch.setattr(safety, "DEFAULT_ON", True)
    seen = []
    monkeypatch.setattr(
        "astrolift_identity.step_up.check_elevation",
        lambda info, **kw: seen.append(kw["action_label"]) or safety_failure(),
    )
    refused = _set(world, app_slug=app.slug, clear=True)
    assert refused.ok is False and seen == ["Make a temporary app permanent"]
    app.refresh_from_db()
    assert app.expires_at is not None


def safety_failure():
    from astrolift_graphql import failure

    return failure("STEP_UP_REQUIRED", "verify")


def test_another_orgs_app_is_not_found(world, other, armed):
    theirs = _temp_app(other, slug="theirs")
    result = _set(world, app_slug=theirs.slug, extend_by="1d")
    assert result.ok is False and result.errors[0].code in {"NOT_FOUND", "PERMISSION_DENIED"}
    theirs.refresh_from_db()
    assert armed == []


def test_an_expired_app_cannot_be_changed(world, armed):
    app = _temp_app(world, expiry_fired_at=timezone.now())
    result = _set(world, app_slug=app.slug, extend_by="1d")
    assert result.ok is False and "expired" in result.errors[0].message


def test_temporary_filter_and_read(world):
    _temp_app(world)
    with _as(world) as info:
        page = RegistryQuery().astrolift_apps_page(
            info, filter=AppsListFilterInput(temporary=True), page=1, page_size=25
        )
        permanent = RegistryQuery().astrolift_apps_page(
            info, filter=AppsListFilterInput(temporary=False), page=1, page_size=25
        )
    assert [a.slug for a in page.items] == ["tmp-app"]
    assert page.items[0].expires_at is not None
    assert "tmp-app" not in {a.slug for a in permanent.items} and permanent.items


# ---------------------------------------------------------------------------
# The timer's decisions, warnings and the expiry
# ---------------------------------------------------------------------------


def _member(world, suffix):
    user = make_user(suffix)
    Member.objects.create(user=user, scope_kind="TEAM", scope_id=world.medops.pk, is_active=True)
    return user


def test_warnings_go_once_each_to_the_team_less_the_muted(world, monkeypatch):
    events = []
    monkeypatch.setattr("core.events.Event.emit", staticmethod(lambda *a, **k: events.append((a, k))))
    alice, bob = _member(world, "tmp-alice"), _member(world, "tmp-bob")
    now = timezone.now()
    app = _temp_app(world, expires_at=now + dt.timedelta(hours=30))
    UserAlertSubscription.objects.create(
        user=bob, registered_app=app, alert_kind=UserAlertSubscription.AlertKind.APP_EXPIRING, enabled=False
    )

    step = expiry.next_step(app.pk, now=now)
    assert step["action"] == "sleep" and abs(step["seconds"] - 6 * 3600) <= 2  # until the 24 h warning

    at_23h = now + dt.timedelta(hours=7)
    step = expiry.next_step(app.pk, now=at_23h)
    assert step["action"] == "sleep" and abs(step["seconds"] - 22 * 3600) <= 2  # until the 1 h warning
    assert list(Notification.objects.filter(kind="app_expiring").values_list("user_id", flat=True)) == [
        alice.pk
    ]
    expiry.next_step(app.pk, now=at_23h)  # again: not sent twice
    assert Notification.objects.filter(kind="app_expiring").count() == 1

    expiry.next_step(app.pk, now=now + dt.timedelta(hours=29, minutes=30))
    assert Notification.objects.filter(kind="app_expiring").count() == 2
    app.refresh_from_db()
    assert app.expiry_warnings_sent == ["1h", "24h"]
    assert [k["payload"]["warning"] for _a, k in events] == ["24h", "1h"]
    assert events[0][1]["payload"]["recipient_user_ids"] == [alice.pk]


def test_an_expiry_set_inside_the_hour_sends_only_the_last_warning(world, monkeypatch):
    monkeypatch.setattr("core.events.Event.emit", staticmethod(lambda *a, **k: None))
    _member(world, "tmp-carol")
    app = _temp_app(world, expires_at=timezone.now() + dt.timedelta(minutes=40))
    expiry.next_step(app.pk)
    assert Notification.objects.filter(kind="app_expiring").count() == 1
    app.refresh_from_db()
    assert app.expiry_warnings_sent == ["1h", "24h"]


def test_failed_warning_rolls_back_delivery_marker_and_notifications(world, monkeypatch):
    _member(world, "tmp-warning-retry")
    app = _temp_app(world, expires_at=timezone.now() + dt.timedelta(minutes=40))

    def fail(*args, **kwargs):
        raise RuntimeError("event persistence unavailable")

    monkeypatch.setattr("core.events.Event.emit", staticmethod(fail))
    with pytest.raises(RuntimeError, match="persistence unavailable"):
        expiry.next_step(app.pk)
    app.refresh_from_db()
    assert app.expiry_warnings_sent == []
    assert not Notification.objects.filter(kind="app_expiring").exists()
    monkeypatch.setattr("core.events.Event.emit", staticmethod(lambda *args, **kwargs: None))
    expiry.next_step(app.pk)
    app.refresh_from_db()
    assert app.expiry_warnings_sent == ["1h", "24h"]
    assert Notification.objects.filter(kind="app_expiring").count() == 1


def test_fire_scales_to_zero_and_keeps_data_for_the_grace(world):
    app = _temp_app(world, expires_at=timezone.now() - dt.timedelta(seconds=1))
    wl = Workload.objects.create(registered_app=app, name="web", slug="web", replicas=3)
    assert expiry.next_step(app.pk)["action"] == "fire"
    assert expiry.fire(app.pk) is True
    app.refresh_from_db()
    wl.refresh_from_db()
    assert app.archived_at is not None and wl.replicas == 0 and wl.pre_archive_replicas == 3
    assert app.data_retained_until - app.expiry_fired_at == dt.timedelta(days=7)
    step = expiry.next_step(app.pk)
    assert step == {"action": "teardown", "guid": str(app.guid), "delete_data": False}
    assert expiry.fire(app.pk) is True  # once: nothing moves on a replay
    app.refresh_from_db()
    assert app.data_retained_until - app.expiry_fired_at == dt.timedelta(days=7)


def test_fire_is_refused_when_the_row_moved(world):
    app = _temp_app(world, expires_at=timezone.now() + dt.timedelta(hours=1))
    assert expiry.fire(app.pk) is False
    RegisteredApp.objects.filter(pk=app.pk).update(expires_at=None)
    assert expiry.fire(app.pk, now=timezone.now() + dt.timedelta(days=1)) is False
    assert expiry.next_step(app.pk) == {"action": "done"}


def test_delete_data_on_expiry_keeps_nothing(world):
    world.org.temporary_app_policy = {"data_grace_days": 2}
    world.org.save(update_fields=["temporary_app_policy"])
    app = _temp_app(world, expires_at=timezone.now(), delete_data_on_expiry=True)
    expiry.fire(app.pk)
    app.refresh_from_db()
    assert app.data_retained_until is None
    assert expiry.next_step(app.pk)["delete_data"] is True


def test_grace_period_then_purge_then_done(world, audit):
    now = timezone.now()
    app = _temp_app(world, expires_at=now - dt.timedelta(minutes=1))
    from astrolift_lifecycle.models import AppEnvironment

    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=world.cluster, name="production")
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="postgres",
        name="db",
        variant="rds",
        backend_ref="rds/db",
    )
    expiry.fire(app.pk, now=now)
    expiry.record_teardown(
        app.pk, {"teardown": {"deployments_cancelled": {"ok": True}, "managed_services": {"ok": True}}}
    )
    RegisteredApp.objects.filter(pk=app.pk).update(deleted_at=now)
    step = expiry.next_step(app.pk, now=now + dt.timedelta(days=1))
    assert step["action"] == "sleep" and abs(step["seconds"] - 6 * 86400) <= 2
    assert expiry.next_step(app.pk, now=now + dt.timedelta(days=8)) == {
        "action": "purge",
        "managed_service_ids": [service.pk],
    }
    fired = [a for a in audit if a.action == "app.expiry.fire"][0].extra
    assert fired["surface"] == "temporal"
    assert fired["kept_managed_services"][0]["name"] == "db"
    assert "deployments_cancelled" in fired["torn_down"]
    assert "managed_services" not in fired["torn_down"]

    ManagedService.objects.filter(pk=service.pk).update(deleted_at=now)
    expiry.record_purge(app.pk, [{"managed_service_id": service.pk, "ok": True}])
    assert RegisteredApp.all_objects.get(pk=app.pk).data_retained_until is None
    assert expiry.next_step(app.pk, now=now + dt.timedelta(days=9)) == {"action": "done"}


def test_sweep_starts_only_missing_timers(world, other, monkeypatch):
    from astrolift_workflows.client import WorkflowRunState

    running = _temp_app(world, slug="running")
    lost = _temp_app(world, slug="lost")
    _temp_app(other, slug="permanent", expires_at=None)
    RegisteredApp.objects.filter(slug="permanent").update(expires_at=None)
    states = {expiry.workflow_id(str(running.guid)): WorkflowRunState(running=True, run_id="r")}
    monkeypatch.setattr(
        "astrolift_workflows.client.workflow_run_state",
        lambda wf: states.get(wf, WorkflowRunState(running=False, run_id="")),
    )
    starts = []
    monkeypatch.setattr(
        "astrolift_workflows.client.signal_or_start_workflow",
        lambda name, args, *, workflow_id, signal_name: starts.append((workflow_id, signal_name)),
    )
    assert expiry.sweep() == 1
    assert starts == [(expiry.workflow_id(str(lost.guid)), "reschedule")]


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def test_mcp_app_reads_expose_the_actual_expiry(world):
    from astrolift_agents.views.mcp import _serialize_agent
    from astrolift_registry.brains import describe_brain

    app = _temp_app(world, app_kind="mimwell", delete_data_on_expiry=True)
    agent = Workload.objects.create(
        registered_app=app, name="worker", slug="worker", kind=Workload.Kind.AGENT
    )
    for data in (describe_brain(app), _serialize_agent(agent, include_package=False)):
        assert data["expires_at"] == app.expires_at.isoformat()
        assert data["delete_data_on_expiry"] is True


WRITE = [SCOPE_MCP_READ, SCOPE_MCP_WRITE, SCOPE_READ_APPS, SCOPE_WRITE_APPS]


def _tool(world, token, name, **arguments):
    from astrolift_agents.tests.test_mcp_gateway import _request
    from astrolift_agents.views.mcp import mcp_gateway

    marker = set_current_api_token(token)
    try:
        with tenant_context(
            TenantContext(organization_id=world.org.pk, team_id=token.team_id, actor_user_id=world.user.pk)
        ):
            response = mcp_gateway(
                _request(world.user, token, "tools/call", {"name": name, "arguments": arguments})
            )
    finally:
        reset_current_api_token(marker)
    return json.loads(response.content)["result"]


def test_agents_extend_but_cannot_make_permanent_until_allowed(world, armed, audit):
    app = _temp_app(world)
    token = _token(world, WRITE)
    extended = _tool(world, token, "astrolift_set_app_expiry", slug=app.slug, extend_by="1d")
    assert extended["isError"] is False, extended
    assert extended["structuredContent"]["expires_at"] is not None
    row = [a for a in audit if a.action == "app.expiry.extend"][-1]
    assert row.extra["surface"] == "mcp" and row.extra["token"] == str(token.guid)

    refused = _tool(world, token, "astrolift_set_app_expiry", slug=app.slug, clear=True)
    assert refused["isError"] is True and "needs a person" in json.dumps(refused)
    app.refresh_from_db()
    assert app.expires_at is not None

    ActionSafetyRule.objects.create(
        organization=world.org, scope_kind="ORG", action="app.expiry.clear", allow_agents=True
    )
    allowed = _tool(world, token, "astrolift_set_app_expiry", slug=app.slug, clear=True)
    assert allowed["isError"] is False, allowed
    app.refresh_from_db()
    assert app.expires_at is None


def test_mcp_tool_stays_within_the_token(world, other, armed):
    reader = _token(world, [SCOPE_MCP_READ, SCOPE_READ_APPS], suffix="r")
    app = _temp_app(world)
    result = _tool(world, reader, "astrolift_set_app_expiry", slug=app.slug, extend_by="1d")
    assert result["isError"] is True and "missing scope" in json.dumps(result)
    theirs = _temp_app(other, slug="theirs")
    result = _tool(
        world, _token(world, WRITE, suffix="w"), "astrolift_set_app_expiry", slug=theirs.slug, extend_by="1d"
    )
    assert result["isError"] is True
    assert armed == []


def test_mcp_surface_can_require_an_expiry(world, armed):
    world.org.temporary_app_policy = {"ttl_required_surfaces": ["mcp"]}
    world.org.save(update_fields=["temporary_app_policy"])
    with expiry_surface("mcp"), _as(world) as _info:
        assert "MCP" in (expiry.permanent_refusal(world.org) or "")
    with _as(world) as _info:
        assert expiry.permanent_refusal(world.org) is None


def expiry_surface(name):
    from astrolift_registry.brain_manage import surface

    return surface(name)
