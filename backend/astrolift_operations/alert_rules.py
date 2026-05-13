"""
Default alert rules + delivery channel routing (#159, spec 08 §10).

Two pure-policy concerns kept in one module:

* **Default rule templates.** When an app is registered, the
  workflow reads ``DEFAULT_RULES`` and seeds an AlertRule per
  template. Templates carry the metric expression, threshold, and
  duration — enough that the workflow can render concrete
  PrometheusRule YAML without this module knowing about Prometheus.
* **Severity → channel routing + suppression.** Given an alert's
  severity and the org's channel routing config, decide which
  channels to notify, whether the alert is currently suppressed
  (active maintenance window or silence schedule), and whether
  this is a duplicate within the de-dup window.

The actual delivery (PagerDuty incident create, Slack webhook
POST, email send) lives in the channel drivers — this module
returns the *list of channels* to notify, plus the de-dup decision.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(StrEnum):
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


# ---- default rules ---------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class AlertRuleTemplate:
    """One default alert rule the registration workflow seeds.

    Stays metric-agnostic — ``expression`` is whatever the platform
    metrics layer accepts (PromQL today; the field is opaque to this
    module). ``for_seconds`` is how long the condition must hold
    before the alert fires.
    """

    slug: str
    title: str
    severity: Severity
    expression: str
    for_seconds: int
    description: str = ""


# Spec 08 §10.2 standard set. Operators may disable / edit after
# seeding; templates here are the seeds, not the canonical state.
DEFAULT_RULES: tuple[AlertRuleTemplate, ...] = (
    AlertRuleTemplate(
        slug="high_5xx_rate",
        title="High error rate (>5% 5xx for 5 min)",
        severity=Severity.CRITICAL,
        expression=(
            'sum(rate(http_requests_total{app="$app",status=~"5.."}[5m]))'
            ' / sum(rate(http_requests_total{app="$app"}[5m])) > 0.05'
        ),
        for_seconds=5 * 60,
    ),
    AlertRuleTemplate(
        slug="high_latency_p99",
        title="High latency (p99 > 1s for 5 min)",
        severity=Severity.WARNING,
        expression=(
            "histogram_quantile(0.99,"
            ' sum(rate(http_request_duration_seconds_bucket{app="$app"}[5m])) by (le))'
            " > 1"
        ),
        for_seconds=5 * 60,
    ),
    AlertRuleTemplate(
        slug="pod_restart_loop",
        title="Pod restart loop",
        severity=Severity.CRITICAL,
        expression=('increase(kube_pod_container_status_restarts_total{namespace=~"$ns"}[15m]) > 5'),
        for_seconds=5 * 60,
    ),
    AlertRuleTemplate(
        slug="oom_killed",
        title="Container OOMKilled",
        severity=Severity.CRITICAL,
        expression=(
            'kube_pod_container_status_last_terminated_reason{namespace=~"$ns",reason="OOMKilled"} == 1'
        ),
        for_seconds=0,
    ),
    # Spec 08 §10.2 broader set (covers #18). Operators may
    # disable / edit after seeding.
    AlertRuleTemplate(
        slug="health_check_failing",
        title="Health check failing for 5 min",
        severity=Severity.CRITICAL,
        expression=('probe_success{app="$app"} == 0'),
        for_seconds=5 * 60,
    ),
    AlertRuleTemplate(
        slug="cron_failure_streak",
        title="Cron failure streak (3 consecutive)",
        severity=Severity.WARNING,
        expression=('sum_over_time(cronjob_failed_runs{app="$app"}[1h]) >= 3'),
        for_seconds=0,
    ),
    AlertRuleTemplate(
        slug="cpu_saturation",
        title="CPU > 90% for 15 min",
        severity=Severity.INFO,
        expression=('avg_over_time(container_cpu_usage_ratio{namespace=~"$ns"}[15m]) > 0.9'),
        for_seconds=15 * 60,
    ),
    AlertRuleTemplate(
        slug="memory_saturation",
        title="Memory > 90% for 15 min",
        severity=Severity.INFO,
        expression=('avg_over_time(container_memory_usage_ratio{namespace=~"$ns"}[15m]) > 0.9'),
        for_seconds=15 * 60,
    ),
)


def render_for_app(
    *,
    app_name: str,
    namespace: str,
    auto_create_disabled: bool = False,
) -> list[AlertRuleTemplate]:
    """Return the templates the workflow should seed for this app.

    ``auto_create_disabled`` is the per-app or per-org opt-out
    (spec 08 §10.2). Returns an empty list if disabled — the
    caller treats that as 'don't seed anything'."""
    if auto_create_disabled:
        return []
    rendered: list[AlertRuleTemplate] = []
    for tpl in DEFAULT_RULES:
        rendered.append(
            dataclasses.replace(
                tpl,
                expression=(tpl.expression.replace("$app", app_name).replace("$ns", namespace)),
            )
        )
    return rendered


# ---- delivery routing -----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ChannelRoute:
    """How one severity routes to channels.

    ``channel_ids`` is opaque (caller resolves to actual channel
    rows). The module just decides which IDs apply for a given
    severity.
    """

    severity: Severity
    channel_ids: tuple[int, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SuppressionWindow:
    """An active or scheduled maintenance window. Optional scoping
    by app and severity — empty means "all"."""

    starts_at: datetime
    ends_at: datetime
    app_name: str = ""
    severity: Severity | None = None  # None = applies to all

    def __post_init__(self) -> None:
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("suppression window timestamps must be tz-aware")
        if self.ends_at < self.starts_at:
            raise ValueError("ends_at must be on or after starts_at")


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """The output of evaluating one alert against its routing."""

    channel_ids: tuple[int, ...]
    suppressed: bool
    suppressed_reason: str
    deduped: bool


def evaluate(
    *,
    severity: Severity,
    fired_at: datetime,
    app_name: str,
    routes: Sequence[ChannelRoute],
    suppressions: Sequence[SuppressionWindow] = (),
    last_delivery_at: datetime | None = None,
    dedup_window_seconds: int = 300,
) -> DeliveryDecision:
    """Decide which channels (if any) to fire for this alert.

    Order: suppression first (maintenance window wins over de-dup),
    then de-dup, then route lookup. We return the channel list
    even when suppressed/deduped so callers can record what *would*
    have fired (UI shows 'X alerts suppressed during maintenance')."""
    if fired_at.tzinfo is None:
        raise ValueError("fired_at must be timezone-aware")

    matched_channels: tuple[int, ...] = ()
    for route in routes:
        if route.severity == severity:
            matched_channels = route.channel_ids
            break

    suppressed = False
    reason = ""
    for window in suppressions:
        if not (window.starts_at <= fired_at <= window.ends_at):
            continue
        if window.app_name and window.app_name != app_name:
            continue
        if window.severity is not None and window.severity != severity:
            continue
        suppressed = True
        reason = "maintenance window"
        break

    deduped = False
    if not suppressed and last_delivery_at is not None:
        if last_delivery_at.tzinfo is None:
            raise ValueError("last_delivery_at must be timezone-aware")
        elapsed = (fired_at - last_delivery_at).total_seconds()
        if elapsed < dedup_window_seconds:
            deduped = True

    return DeliveryDecision(
        channel_ids=matched_channels,
        suppressed=suppressed,
        suppressed_reason=reason,
        deduped=deduped,
    )


def should_fire(decision: DeliveryDecision) -> bool:
    """Convenience: ``True`` iff the caller should actually call the
    channel drivers for this alert."""
    return bool(decision.channel_ids) and not decision.suppressed and not decision.deduped
