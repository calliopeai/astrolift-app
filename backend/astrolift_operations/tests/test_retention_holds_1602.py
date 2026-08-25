"""Retention holds: the data source `is_held` never had (#1602 step 2).

`observability_retention.is_held()` had correct match logic and no input --
no table, no query, no row, so its own test was the only caller that ever
passed it a hold.

Holds land before the eviction driver on purpose. They are how an operator
says "an incident is live, do not delete this window yet", so shipping
eviction first would mean the first activation deletes data an incident is
depending on with nothing available to stop it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from astrolift_operations.models import ObservabilityRetentionHold
from astrolift_operations.observability_retention import is_held
from astrolift_operations.retention_holds import active_holds_for
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    PlaceObservabilityRetentionHoldInput,
    ReleaseObservabilityRetentionHoldInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Holds", slug="holds-org")


@pytest.fixture
def other_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Other", slug="other-holds-org")


@pytest.fixture
def actor():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(username="holder@test", email="holder@test")


@pytest.fixture
def fake_info(actor):
    """Minimal `info`-shaped object every mutation resolver accepts."""
    from types import SimpleNamespace

    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor)))


def _tenant(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _err(result) -> str:
    code = result.errors[0].code
    return getattr(code, "value", code)


def _hold(org, **kw):
    defaults = {
        "organization": org,
        "stream": "*",
        "starts_at": NOW - dt.timedelta(days=3),
        "ends_at": NOW - dt.timedelta(days=1),
        "reason": "incident 42",
    }
    return ObservabilityRetentionHold.objects.create(**{**defaults, **kw})


# ---------------------------------------------------------------------------
# The projection onto the policy
# ---------------------------------------------------------------------------


def test_a_row_becomes_a_policy_hold_that_is_held_accepts(org):
    """The whole point: the pure policy now has a real input."""
    _hold(org, stream="log")

    holds = active_holds_for(org)

    assert len(holds) == 1
    assert is_held(
        stream="log",
        timestamp=NOW - dt.timedelta(days=2),
        resource_kind="",
        resource_id="",
        holds=holds,
    )


def test_a_timestamp_outside_the_window_is_not_held(org):
    _hold(org, stream="log")

    assert not is_held(
        stream="log",
        timestamp=NOW,
        resource_kind="",
        resource_id="",
        holds=active_holds_for(org),
    )


def test_the_wildcard_stream_holds_every_stream(org):
    """An operator holding everything during an incident must not have to
    place four rows and risk three of them."""
    _hold(org, stream="*")
    holds = active_holds_for(org)

    for stream in ("log", "metric_raw", "metric_rollup", "trace"):
        assert is_held(
            stream=stream,
            timestamp=NOW - dt.timedelta(days=2),
            resource_kind="",
            resource_id="",
            holds=holds,
        )


def test_a_past_window_is_still_active(org):
    """The trap this design avoids. A hold's window bounds a slice of
    *data*, not a period during which the hold applies -- so a hold over
    last Tuesday stays in force indefinitely. Filtering on `ends_at >= now`
    is the obvious reading and would delete the exact data the hold was
    placed to keep, on a timer, silently.
    """
    _hold(org, starts_at=NOW - dt.timedelta(days=400), ends_at=NOW - dt.timedelta(days=390))

    assert len(active_holds_for(org)) == 1


def test_a_released_hold_is_not_active(org):
    hold = _hold(org)
    hold.soft_delete()

    assert active_holds_for(org) == []


def test_holds_do_not_leak_across_orgs(org, other_org):
    _hold(other_org, stream="log")

    assert active_holds_for(org) == []


def test_resource_scoping_round_trips(org):
    _hold(org, stream="log", resource_kind="App", resource_id="42")
    holds = active_holds_for(org)
    at = NOW - dt.timedelta(days=2)

    assert is_held(stream="log", timestamp=at, resource_kind="App", resource_id="42", holds=holds)
    assert not is_held(stream="log", timestamp=at, resource_kind="App", resource_id="43", holds=holds)


def test_the_model_choices_match_the_policys_streams():
    """Duplicated on purpose -- a migration freezes whatever the tuple said
    the day it ran -- so the duplication needs a guard."""
    from astrolift_operations.observability_retention import ALL_STREAMS

    choices = {c for c, _ in ObservabilityRetentionHold.Stream.choices}

    assert choices == set(ALL_STREAMS) | {"*"}


# ---------------------------------------------------------------------------
# The mutations
# ---------------------------------------------------------------------------


def _place(org, actor, fake_info, **kw):
    defaults = {
        "starts_at": NOW - dt.timedelta(days=3),
        "ends_at": NOW - dt.timedelta(days=1),
        "reason": "incident 42",
    }
    with _tenant(org, actor):
        return OperationsMutation().place_observability_retention_hold(
            fake_info,
            input=PlaceObservabilityRetentionHoldInput(**{**defaults, **kw}),
        )


def test_placing_a_hold_creates_it(org, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _place(org, actor, fake_info, stream="log")

    assert result.ok, result.errors
    assert result.data.stream == "log"
    assert result.data.released is False
    assert ObservabilityRetentionHold.objects.filter(organization=org).count() == 1


def test_a_hold_records_who_placed_it(org, actor, fake_info, permission_resolver):
    """A hold nobody can attribute is a hold nobody will dare release."""
    permission_resolver.grant(Permission.ORG_UPDATE)

    _place(org, actor, fake_info)

    assert ObservabilityRetentionHold.objects.get().placed_by_id == actor.id


def test_a_reason_is_required(org, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _place(org, actor, fake_info, reason="   ")

    assert not result.ok
    assert _err(result) == "VALIDATION"
    assert not ObservabilityRetentionHold.objects.exists()


def test_an_inverted_window_is_refused(org, actor, fake_info, permission_resolver):
    """The policy dataclass raises on this, and a mutation must never
    raise -- so it is caught here and returned as an envelope."""
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _place(
        org,
        actor,
        fake_info,
        starts_at=NOW,
        ends_at=NOW - dt.timedelta(days=1),
    )

    assert not result.ok
    assert _err(result) == "VALIDATION"


def test_an_unknown_stream_is_refused(org, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _place(org, actor, fake_info, stream="metrics")

    assert not result.ok
    assert _err(result) == "VALIDATION"


def test_placing_requires_org_update(org, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.ORG_READ)

    result = _place(org, actor, fake_info)

    assert not result.ok
    assert _err(result) == "PERMISSION_DENIED"


def test_releasing_soft_deletes_rather_than_removing(org, actor, fake_info, permission_resolver):
    """Matching unmute: the history of past holds stays queryable, which is
    the point when a review asks why a window was or was not kept."""
    permission_resolver.grant(Permission.ORG_UPDATE)
    hold = _hold(org)

    with _tenant(org, actor):
        result = OperationsMutation().release_observability_retention_hold(
            fake_info,
            input=ReleaseObservabilityRetentionHoldInput(hold_id=str(hold.guid)),
        )

    assert result.ok, result.errors
    assert result.data.released is True
    # Through `all_objects`, not `objects`: the default manager filters
    # soft-deleted rows, so asserting survival through it would be asking
    # the wrong manager and would fail on a correct soft delete.
    assert ObservabilityRetentionHold.all_objects.filter(pk=hold.pk).exists(), "row must survive"
    assert ObservabilityRetentionHold.objects.filter(pk=hold.pk).exists() is False
    assert active_holds_for(org) == []


def test_another_orgs_hold_cannot_be_released(org, other_org, actor, fake_info, permission_resolver):
    """A bare guid must not let one tenant release another's hold, which
    would let their incident data be evicted."""
    permission_resolver.grant(Permission.ORG_UPDATE)
    foreign = _hold(other_org)

    with _tenant(org, actor):
        result = OperationsMutation().release_observability_retention_hold(
            fake_info,
            input=ReleaseObservabilityRetentionHoldInput(hold_id=str(foreign.guid)),
        )

    assert not result.ok
    assert _err(result) == "NOT_FOUND"
    assert active_holds_for(other_org) != []


def test_no_tenant_context_denies_rather_than_defaulting(actor, fake_info, permission_resolver):
    """`@tenant_scoped()` asserts a tenant exists; it does not filter
    anything. A None org must be deny-by-default, never "all rows"."""
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = OperationsMutation().place_observability_retention_hold(
        fake_info,
        input=PlaceObservabilityRetentionHoldInput(
            starts_at=NOW - dt.timedelta(days=1),
            ends_at=NOW,
            reason="x",
        ),
    )

    assert not result.ok
    assert not ObservabilityRetentionHold.objects.exists()
