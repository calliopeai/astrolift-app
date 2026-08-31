"""A failed provision has to say so (#1677).

`ProvisioningStatus.FAILED` and `RegisteredApp.provisioning_error` both existed
from the start, and **nothing ever reached one or wrote the other**. So an app
whose provision had died twenty minutes ago rendered exactly like one still in
flight: `Status: provisioning`, `provisioningError: ""`, no workloads.

That is not a cosmetic gap. There is no `astro app events` or `astro app audit`
verb implemented, so from the CLI a tenant has no route to the cause at all --
the only way to find it was the worker's CloudWatch logs, which a tenant of a
BYOC install does not have. It cost a live debugging session to learn that an
`ecr:CreateRepository` denial was behind it.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.app_lifecycle import _mark_app_failed_sync

pytestmark = pytest.mark.django_db

DENIAL = (
    "AccessDeniedException: User: arn:aws:sts::790347818717:assumed-role/"
    "conflictastro-astrolift-task/x is not authorized to perform: "
    "ecr:CreateRepository"
)


def _app(suffix: str, status: str = "provisioning") -> RegisteredApp:
    org = Organization.objects.create(name=f"Acme {suffix}", slug=f"acme-fail-{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-fail-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-fail-{suffix}")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Shop",
        slug=f"shop-fail-{suffix}",
        provisioning_status=status,
    )


def test_a_failed_provision_records_the_reason() -> None:
    app = _app("reason")

    _mark_app_failed_sync(app.pk, DENIAL)

    app.refresh_from_db()
    assert app.provisioning_status == RegisteredApp.ProvisioningStatus.FAILED.value
    # The driver's own words, not a generic "provision failed" -- the action
    # name is the whole diagnostic value.
    assert "ecr:CreateRepository" in app.provisioning_error


def test_the_reason_is_truncated_rather_than_unbounded() -> None:
    """A driver traceback can run to kilobytes and this is rendered inline by
    both the CLI and the UI."""
    app = _app("long")

    _mark_app_failed_sync(app.pk, "x" * 5000)

    app.refresh_from_db()
    assert len(app.provisioning_error) == 2000


def test_marking_failed_twice_is_a_no_op() -> None:
    """The activity runs on the failure path, where a retry re-entering it must
    not raise a second, more confusing error."""
    app = _app("twice")
    _mark_app_failed_sync(app.pk, DENIAL)

    _mark_app_failed_sync(app.pk, "something else entirely")

    app.refresh_from_db()
    # First reason wins; the second call neither raises nor overwrites.
    assert "ecr:CreateRepository" in app.provisioning_error


def test_reaching_ready_clears_a_previous_failure() -> None:
    """`failed → provisioning → ready` is a legal path, and a stale error on a
    now-healthy app would be worse than none."""
    app = _app("recover")
    _mark_app_failed_sync(app.pk, DENIAL)

    app.refresh_from_db()
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.PROVISIONING)
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.READY)

    app.refresh_from_db()
    assert app.provisioning_status == RegisteredApp.ProvisioningStatus.READY.value
    assert app.provisioning_error == ""


def test_a_transition_that_is_no_longer_legal_does_not_mask_the_failure() -> None:
    """A teardown can race the failure. `deregistered → failed` is not a legal
    transition, and raising here would replace a useful provisioning error with
    a confusing state-machine one."""
    app = _app("raced", status="deregistered")

    with pytest.raises(ValueError):
        _mark_app_failed_sync(app.pk, DENIAL)

    # The async activity is what swallows it; the sync helper stays honest so
    # the rule is visible in one place.
    app.refresh_from_db()
    assert app.provisioning_status == "deregistered"
