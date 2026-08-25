"""An org's configured log retention actually takes effect (#1602).

`observability_retention.effective_for` has resolved the platform default,
the per-org override and incident holds since the feature was specced, and
nothing called it. Four `Organization` columns are settable through
`updateOrganization` and were read by nobody, so an operator could set a
retention and the data lived forever.

AWS only, per the AWS-first rule, and that is the right shape rather than a
compromise: CloudWatch is the one backend here with a **native** retention
primitive. `PutRetentionPolicy` sets the window and AWS ages the data out
itself, so there is nothing for the platform to delete on a schedule and a
platform-side delete loop would be strictly worse than the one AWS already
runs.
"""

from __future__ import annotations

import pytest
from _sdk.observability.cloudwatch_logs import (
    CloudWatchLogsConfig,
    CloudWatchLogsRetentionDriver,
)


class _Logs:
    """Stands in for the boto3 ``logs`` client."""

    def __init__(self, current=None):
        self.current = current
        self.calls: list[dict] = []

    def describe_log_groups(self, logGroupNamePrefix):  # noqa: N803 - boto3's name
        group = {"logGroupName": logGroupNamePrefix}
        if self.current is not None:
            group["retentionInDays"] = self.current
        return {"logGroups": [group]}

    def put_retention_policy(self, **kwargs):
        self.calls.append(kwargs)


def _driver(logs):
    return CloudWatchLogsRetentionDriver(
        config=CloudWatchLogsConfig(region="us-west-2", log_group="/aws/x/application", client=logs)
    )


# ---- snapping to what CloudWatch accepts ---------------------------------


def test_an_exact_window_is_used_as_is():
    assert CloudWatchLogsRetentionDriver.snap_days(30) == 30


def test_an_unsupported_window_rounds_up_not_down():
    """The important direction. An org asking for 100 days gets 120, not 90:
    under-retaining is a compliance failure and over-retaining is a cost
    line, and only one of those is reversible."""
    assert CloudWatchLogsRetentionDriver.snap_days(100) == 120
    assert CloudWatchLogsRetentionDriver.snap_days(31) == 60
    assert CloudWatchLogsRetentionDriver.snap_days(1) == 1


def test_a_window_past_the_maximum_clamps_to_it():
    assert CloudWatchLogsRetentionDriver.snap_days(99999) == 3653


def test_a_nonsense_window_is_refused():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            CloudWatchLogsRetentionDriver.snap_days(bad)


# ---- applying -----------------------------------------------------------


def test_it_sets_the_policy_on_a_group_that_has_none():
    """CloudWatch's own default is no policy, meaning keep forever, which is
    the state every group is in until something sets one."""
    logs = _Logs(current=None)

    result = _driver(logs).apply_retention(30)

    assert result == {"changed": True, "days": 30, "previous": None}
    assert logs.calls == [{"logGroupName": "/aws/x/application", "retentionInDays": 30}]


def test_it_is_idempotent():
    """A scheduled task that logs 'applied retention' every tick trains
    operators to ignore it."""
    logs = _Logs(current=30)

    result = _driver(logs).apply_retention(30)

    assert result == {"changed": False, "days": 30}
    assert logs.calls == []


def test_an_unsupported_request_is_applied_at_the_snapped_value():
    logs = _Logs(current=None)

    _driver(logs).apply_retention(100)

    assert logs.calls[0]["retentionInDays"] == 120


def test_it_reports_the_previous_window_on_a_change():
    logs = _Logs(current=365)

    result = _driver(logs).apply_retention(30)

    assert result["previous"] == 365


# ---- the resolver finally has a caller ----------------------------------


def test_the_org_override_wins_over_the_platform_default():
    from astrolift_operations.observability_retention import effective_for

    assert effective_for(stream="log", org_override_days=7).days == 7
    assert effective_for(stream="log", org_override_days=None).days == 30


def test_a_nonsense_override_falls_back_rather_than_evicting_everything():
    """The resolver's own guard, and worth a test now that something acts on
    the answer: a typo in the admin UI must not set retention to zero days."""
    from astrolift_operations.observability_retention import effective_for

    assert effective_for(stream="log", org_override_days=0).days == 30
    assert effective_for(stream="log", org_override_days=-5).days == 30
