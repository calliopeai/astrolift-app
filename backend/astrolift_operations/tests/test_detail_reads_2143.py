"""Direct event reads and complete app alert counts against PostgreSQL."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_identity.models import Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.models import AlertEvent, AlertRule, Event
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_operations.tests import test_access_scopes_2107 as access_tests
from astrolift_operations.tests.test_access_scopes_2107 import (
    bearer,
    grant,
    selected_sibling,
)
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import ScopeWorld, make_info

world = access_tests.world
no_index = access_tests.no_index
pytestmark = pytest.mark.django_db


def row_ids(w):
    return (
        Event.objects.get(registered_app=w.medops_app).guid,
        w.rules[0].guid,
        AlertEvent.objects.get(rule=w.rules[0]).guid,
    )


def read_details(w, ids):
    q, info = OperationsQuery(), make_info(w.user)
    return (
        q.astrolift_event(info, id=ids[0]),
        q.astrolift_alert_rule(info, id=ids[1]),
        q.astrolift_alert_event(info, id=ids[2]),
    )


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_direct_reads_resolve_actual_owner_beyond_recent_caps(world, kind):
    ids = row_ids(world)
    Event.objects.bulk_create(
        [
            Event(organization=world.org, registered_app=world.medops_app, event_type="newer")
            for _ in range(210)
        ]
    )
    AlertRule.objects.bulk_create(
        [
            AlertRule(
                organization=world.org, name=f"newer-{i}", target="app", target_id=world.medops_app.slug
            )
            for i in range(210)
        ]
    )
    AlertEvent.objects.bulk_create(
        [AlertEvent(organization=world.org, rule=world.rules[0], fired_at=timezone.now()) for _ in range(110)]
    )
    grant(world, kind)
    with selected_sibling(world):
        assert tuple(str(row.id) for row in read_details(world, ids)) == tuple(map(str, ids))
        assert not set(map(str, ids[:1])).intersection(
            str(row.id) for row in OperationsQuery().astrolift_events(make_info(world.user), limit=200)
        )


@pytest.mark.parametrize("credential", [False, True])
def test_direct_reads_refuse_sibling_and_foreign_owners(world, credential):
    grant(world, "ORG" if credential else "TEAM")
    foreign = ScopeWorld("foreign-2143")
    rule = AlertRule.objects.create(
        organization=foreign.org, name="foreign", target="app", target_id=foreign.medops_app.slug
    )
    foreign_ids = (
        Event.objects.create(
            organization=foreign.org, registered_app=foreign.medops_app, event_type="secret"
        ).guid,
        rule.guid,
        AlertEvent.objects.create(organization=foreign.org, rule=rule, fired_at=timezone.now()).guid,
    )
    sibling_ids = (
        Event.objects.get(registered_app=world.platform_app).guid,
        world.rules[1].guid,
        AlertEvent.objects.get(rule=world.rules[1]).guid,
    )
    with selected_sibling(world):
        if credential:
            with bearer(world):
                assert read_details(world, sibling_ids) == (None, None, None)
                assert read_details(world, foreign_ids) == (None, None, None)
        else:
            assert read_details(world, sibling_ids) == (None, None, None)
            assert read_details(world, foreign_ids) == (None, None, None)


def test_deleted_rules_events_and_app_owners_are_hidden(world):
    grant(world)
    ids = row_ids(world)
    world.rules[0].soft_delete()
    with selected_sibling(world):
        assert read_details(world, ids)[1:] == (None, None)
    world.medops_app.soft_delete()
    with selected_sibling(world):
        assert read_details(world, ids) == (None, None, None)
        assert (
            OperationsQuery()
            .astrolift_alert_event_summary(make_info(world.user), app_slug=world.medops_app.slug)
            .unresolved_count
            == 0
        )


def test_unknown_detail_ids_are_null_and_unauthorized_actor_is_denied(world):
    with selected_sibling(world), pytest.raises(PermissionDenied):
        read_details(world, (uuid4(), uuid4(), uuid4()))
    grant(world)
    with selected_sibling(world):
        assert read_details(world, (uuid4(), uuid4(), uuid4())) == (None, None, None)


def test_app_alert_filters_and_counts_precede_paging_and_limits(world):
    grant(world, "ORG")
    q, info = OperationsQuery(), make_info(world.user)
    AlertEvent.objects.bulk_create(
        [
            AlertEvent(
                organization=world.org, rule=world.rules[0], severity="critical", fired_at=timezone.now()
            )
            for _ in range(60)
        ]
        + [
            AlertEvent(
                organization=world.org, rule=world.rules[1], severity="critical", fired_at=timezone.now()
            )
            for _ in range(70)
        ]
    )
    with selected_sibling(world):
        summary = q.astrolift_alert_event_summary(info, app_slug=world.medops_app.slug)
        assert (summary.unresolved_count, summary.critical_count) == (61, 60)
        page = q.astrolift_alert_events_page(info, app_slug=world.medops_app.slug, limit=10)
        assert page.total_count == 61 and page.next_cursor
        seen = []
        while True:
            seen.extend(str(row.id) for row in page.items)
            assert {str(row.rule_id) for row in page.items} == {str(world.rules[0].guid)}
            if not page.next_cursor:
                break
            page = q.astrolift_alert_events_page(
                info, app_slug=world.medops_app.slug, limit=10, after=page.next_cursor
            )
        assert len(seen) == len(set(seen)) == 61
        assert q.astrolift_alert_events_page(info, app_slug="missing", limit=10).total_count == 0
        rules = q.astrolift_alert_rules_page(info, app_slug=world.medops_app.slug, limit=1)
        assert rules.total_count == 1 and str(rules.items[0].id) == str(world.rules[0].guid)


@pytest.mark.parametrize("target", ["env", "workload"])
def test_app_filter_resolves_guid_targets_and_excludes_ambiguous_names(world, target):
    grant(world, "ORG")
    row = world.environments[0] if target == "env" else world.workloads[0]
    name = row.name if target == "env" else row.slug
    specific = AlertRule.objects.create(
        organization=world.org, name="guid-target", target=target, target_id=str(row.guid)
    )
    ambiguous = AlertRule.objects.create(
        organization=world.org, name="ambiguous-target", target=target, target_id=name
    )
    AlertEvent.objects.create(organization=world.org, rule=specific, fired_at=timezone.now())
    AlertEvent.objects.create(organization=world.org, rule=ambiguous, fired_at=timezone.now())
    with selected_sibling(world):
        page = OperationsQuery().astrolift_alert_rules_page(
            make_info(world.user), app_slug=world.medops_app.slug
        )
        assert {str(r.id) for r in page.items} == {str(world.rules[0].guid), str(specific.guid)}


def test_app_summary_and_singular_alert_honor_persisted_environment_policy(world):
    grant(world, "ORG")
    stage = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="staging", tenant_cluster=world.cluster
    )
    rule = AlertRule.objects.create(
        organization=world.org, name="stage", target="env", target_id=str(stage.guid)
    )
    firing = AlertEvent.objects.create(
        organization=world.org, rule=rule, fired_at=timezone.now(), severity="critical"
    )
    Policy.objects.create(
        organization=world.org,
        name="hide staging",
        slug="hide-stage-2143",
        scope_level="ORG",
        scope_id=world.org.pk,
        effect="DENY",
        action_pattern=Permission.APP_READ.value,
        resource_pattern={"env": ["staging"]},
        actor_pattern={},
        conditions=[],
    )
    with selected_sibling(world):
        q, info = OperationsQuery(), make_info(world.user)
        assert q.astrolift_alert_rule(info, id=rule.guid) is None
        assert q.astrolift_alert_event(info, id=firing.guid) is None
        assert q.astrolift_alert_event_summary(info, app_slug=world.medops_app.slug).unresolved_count == 0
        assert q.astrolift_alert_events_page(info, app_slug=world.medops_app.slug).total_count == 0
        assert q.astrolift_alert_event_summary(info, app_slug=world.platform_app.slug).unresolved_count == 1


def test_new_fields_execute_through_the_merged_schema(world):
    from config.schema import schema

    grant(world)
    ids = row_ids(world)
    with selected_sibling(world):
        result = schema.execute_sync(
            """query($event: GUID!, $rule: GUID!, $alert: GUID!, $app: String!) {
              astroliftEvent(id:$event) { id eventType }
              astroliftAlertRule(id:$rule) { id name }
              astroliftAlertEvent(id:$alert) { id ruleId }
              astroliftAlertEventSummary(appSlug:$app) { unresolvedCount criticalCount }
              astroliftAlertEventsPage(appSlug:$app, limit:1) { totalCount items { id } }
            }""",
            variable_values={
                "event": str(ids[0]),
                "rule": str(ids[1]),
                "alert": str(ids[2]),
                "app": world.medops_app.slug,
            },
            context_value=SimpleNamespace(user=world.user, request=SimpleNamespace(user=world.user)),
        )
    assert not result.errors
    assert result.data["astroliftEvent"]["id"] == str(ids[0])
    assert result.data["astroliftAlertEventSummary"] == {"unresolvedCount": 1, "criticalCount": 0}
    assert result.data["astroliftAlertEventsPage"]["totalCount"] == 1


def test_app_alert_counts_keep_the_team_bearer_ceiling(world):
    grant(world, "ORG")
    with selected_sibling(world), bearer(world):
        q, info = OperationsQuery(), make_info(world.user)
        assert q.astrolift_alert_event_summary(info, app_slug=world.medops_app.slug).unresolved_count == 1
        assert q.astrolift_alert_event_summary(info, app_slug=world.platform_app.slug).unresolved_count == 0
        assert q.astrolift_alert_events_page(info, app_slug=world.platform_app.slug).total_count == 0


@pytest.mark.parametrize(
    "corruption", ["wrong-environment", "foreign-cluster", "deleted-cluster", "deleted-service"]
)
def test_app_alert_filter_keeps_only_live_private_service_owners(world, corruption):
    from astrolift_identity.models import Organization
    from astrolift_services.models import ManagedService

    grant(world, "ORG")
    service = ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=world.environments[0],
        name="cache",
        kind="redis",
    )
    rule = AlertRule.objects.create(
        organization=world.org, name="private", target="global", managed_service=service
    )
    AlertEvent.objects.create(organization=world.org, rule=rule, fired_at=timezone.now())
    with selected_sibling(world):
        assert (
            OperationsQuery()
            .astrolift_alert_event_summary(make_info(world.user), app_slug=world.medops_app.slug)
            .unresolved_count
            == 2
        )
    if corruption == "wrong-environment":
        service.app_environment = world.environments[1]
        service.save(update_fields=["app_environment", "updated_at", "version"])
    elif corruption == "foreign-cluster":
        world.cluster.organization = Organization.objects.create(name="Foreign", slug="foreign-provider-2143")
        world.cluster.save(update_fields=["organization", "updated_at", "version"])
    elif corruption == "deleted-cluster":
        world.cluster.soft_delete()
    else:
        service.soft_delete()
    with selected_sibling(world):
        q, info = OperationsQuery(), make_info(world.user)
        assert q.astrolift_alert_event_summary(info, app_slug=world.medops_app.slug).unresolved_count == 1
        assert q.astrolift_alert_events_page(info, app_slug=world.medops_app.slug).total_count == 1
