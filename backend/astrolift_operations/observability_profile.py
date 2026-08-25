"""
ObservabilityProfile policy (#9, spec 08 §3.4 + §12).

Pure-Python policy. The ``ObservabilityProfile`` Django model
and its create/update mutations consult this module for:

* **Driver registry** — known log/metrics/trace drivers and which
  ones can be multiplexed.
* **Per-driver config validation** — required fields per driver
  kind, refusing typos before persistence.
* **Retention bounds** — defaults + maximums for logs/metrics/
  traces/events/audit/workflow histories per spec §12.
* **Multiplexer rules** — stacked-driver mode validates each
  child driver, rejects empty stack, and enforces no-cycles.

The actual driver implementations live in
``astrolift_drivers/observability/*``; this module is the
shape contract orgs configure against.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from enum import StrEnum


class ObservabilityProfileError(ValueError):
    pass


# ---- driver registries ---------------------------------------------


class LogDriver(StrEnum):
    """Spec 08 §3.4: known log backends."""

    LOKI = "loki"
    OPENSEARCH = "opensearch"
    CLOUDWATCH_LOGS = "cloudwatch_logs"
    GCP_CLOUD_LOGGING = "gcp_cloud_logging"
    AZURE_MONITOR_LOGS = "azure_monitor_logs"
    OTLP_HTTP = "otlp_http"
    """Generic OTLP log exporter for users bringing their own
    backend."""

    MULTIPLEXER = "multiplexer"
    """Stacks multiple log drivers — fans out writes, picks one
    for reads (configured)."""


class MetricsDriver(StrEnum):
    PROMETHEUS = "prometheus"
    """Self-hosted Prometheus on the cluster."""

    MANAGED_PROMETHEUS = "managed_prometheus"
    """AMP / Google Managed Prometheus / Azure Monitor for
    Prometheus. Default per the user's product positioning —
    boilerworks apps are already Prometheus-native."""

    DATADOG = "datadog"
    NEW_RELIC = "new_relic"
    OTLP_HTTP = "otlp_http"
    MULTIPLEXER = "multiplexer"


class TraceDriver(StrEnum):
    TEMPO = "tempo"
    JAEGER = "jaeger"
    OTLP_HTTP = "otlp_http"
    DATADOG = "datadog"
    MULTIPLEXER = "multiplexer"


# ---- per-driver required-config keys -------------------------------


_REQUIRED_KEYS_BY_LOG_DRIVER: dict[LogDriver, frozenset[str]] = {
    LogDriver.LOKI: frozenset({"endpoint"}),
    LogDriver.OPENSEARCH: frozenset({"endpoint", "index_prefix"}),
    LogDriver.CLOUDWATCH_LOGS: frozenset({"region", "log_group"}),
    LogDriver.GCP_CLOUD_LOGGING: frozenset({"project_id"}),
    LogDriver.AZURE_MONITOR_LOGS: frozenset({"workspace_id", "shared_key_secret_ref"}),
    LogDriver.OTLP_HTTP: frozenset({"endpoint"}),
    LogDriver.MULTIPLEXER: frozenset({"children"}),
}

_REQUIRED_KEYS_BY_METRICS_DRIVER: dict[MetricsDriver, frozenset[str]] = {
    MetricsDriver.PROMETHEUS: frozenset({"endpoint"}),
    MetricsDriver.MANAGED_PROMETHEUS: frozenset({"workspace_url"}),
    MetricsDriver.DATADOG: frozenset({"api_key_secret_ref", "site"}),
    MetricsDriver.NEW_RELIC: frozenset({"api_key_secret_ref"}),
    MetricsDriver.OTLP_HTTP: frozenset({"endpoint"}),
    MetricsDriver.MULTIPLEXER: frozenset({"children"}),
}

_REQUIRED_KEYS_BY_TRACE_DRIVER: dict[TraceDriver, frozenset[str]] = {
    TraceDriver.TEMPO: frozenset({"endpoint"}),
    TraceDriver.JAEGER: frozenset({"endpoint"}),
    TraceDriver.OTLP_HTTP: frozenset({"endpoint"}),
    TraceDriver.DATADOG: frozenset({"api_key_secret_ref", "site"}),
    TraceDriver.MULTIPLEXER: frozenset({"children"}),
}


# ---- retention defaults + maxes (spec §12) -------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionWindow:
    default_days: int
    max_days: int

    def __post_init__(self) -> None:
        if self.default_days <= 0:
            raise ObservabilityProfileError(f"default_days must be > 0, got {self.default_days}")
        if self.max_days < self.default_days:
            raise ObservabilityProfileError(f"max_days {self.max_days} < default_days {self.default_days}")


# Spec §12 — these are the tier defaults. max_days is what an org
# admin can dial up to; values higher than this require platform-
# level approval (separate ticket).
RETENTION_LOGS = RetentionWindow(default_days=30, max_days=365)
RETENTION_METRICS = RetentionWindow(default_days=30, max_days=365)
RETENTION_METRICS_ROLLUP = RetentionWindow(default_days=365, max_days=1095)
"""Rolled-up metrics, kept longer than raw and needing their own window.

`Organization.metrics_rollup_retention_days_default` has defaulted to 365
since it was added, and the only ceiling available to bound it was
`RETENTION_METRICS.max_days`, which is also 365. So the column's default
sat exactly on its ceiling and no operator could ever raise it -- a knob
whose only legal value was the one it already had (#1602).

Longer than raw because that is the point of a rollup: a 5-minute
aggregate costs a fraction of the samples it replaces, so year-over-year
capacity trends stay affordable long after the raw series are gone. Three
years is the ceiling because a rollup is still per-series storage, not
free.
"""
RETENTION_TRACES = RetentionWindow(default_days=14, max_days=90)
RETENTION_EVENTS = RetentionWindow(default_days=90, max_days=730)
RETENTION_AUDIT = RetentionWindow(default_days=365, max_days=2555)
"""Audit max = 7 years. Some compliance regimes (HIPAA / SOX)
require it; can't dial below default_days even by org admin."""

RETENTION_WORKFLOWS = RetentionWindow(default_days=90, max_days=365)


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionConfig:
    """Per-profile retention windows. All days_*. Validated
    against the bounds above."""

    logs_days: int = RETENTION_LOGS.default_days
    metrics_days: int = RETENTION_METRICS.default_days
    traces_days: int = RETENTION_TRACES.default_days
    events_days: int = RETENTION_EVENTS.default_days
    audit_days: int = RETENTION_AUDIT.default_days
    workflows_days: int = RETENTION_WORKFLOWS.default_days

    def __post_init__(self) -> None:
        for kind, value, window in (
            ("logs", self.logs_days, RETENTION_LOGS),
            ("metrics", self.metrics_days, RETENTION_METRICS),
            ("traces", self.traces_days, RETENTION_TRACES),
            ("events", self.events_days, RETENTION_EVENTS),
            ("audit", self.audit_days, RETENTION_AUDIT),
            ("workflows", self.workflows_days, RETENTION_WORKFLOWS),
        ):
            if value <= 0:
                raise ObservabilityProfileError(f"{kind}_days must be > 0, got {value}")
            if value > window.max_days:
                raise ObservabilityProfileError(f"{kind}_days {value} exceeds max {window.max_days}")


# ---- driver config validation --------------------------------------


def _validate_required_keys(
    *,
    driver_label: str,
    required: frozenset[str],
    config: Mapping,
) -> None:
    missing = required - set(config.keys())
    if missing:
        raise ObservabilityProfileError(f"{driver_label} config missing required keys: {sorted(missing)}")


def _validate_multiplexer_children(
    *,
    children: object,
    parent_kind: str,
    parse_child: callable,
) -> None:
    """Multiplexer: 'children' must be a list of dicts each with
    ``driver`` + ``config``. Each child gets recursively validated;
    children may NOT themselves be multiplexers (no nesting,
    avoids cycles)."""
    if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
        raise ObservabilityProfileError(f"{parent_kind} multiplexer 'children' must be a list")
    if not children:
        raise ObservabilityProfileError(
            f"{parent_kind} multiplexer 'children' cannot be empty "
            "(use a non-multiplexer driver if you only have one)"
        )
    for i, child in enumerate(children):
        if not isinstance(child, Mapping):
            raise ObservabilityProfileError(
                f"{parent_kind} multiplexer children[{i}] must be a mapping with 'driver' and 'config' keys"
            )
        if "driver" not in child or "config" not in child:
            raise ObservabilityProfileError(
                f"{parent_kind} multiplexer children[{i}] missing 'driver' or 'config'"
            )
        # Children can't be multiplexers — no nesting (cycle defense)
        if child["driver"] == "multiplexer":
            raise ObservabilityProfileError(
                f"{parent_kind} multiplexer children cannot be "
                "multiplexers themselves (nesting not supported)"
            )
        # Validate the child is a known driver of the right kind
        parse_child(child["driver"], child["config"])


def validate_log_driver_config(
    *,
    driver: str,
    config: Mapping,
) -> LogDriver:
    """Parse + validate a log driver config. Returns the typed
    enum on success."""
    try:
        kind = LogDriver(driver)
    except ValueError as exc:
        raise ObservabilityProfileError(
            f"unknown log driver {driver!r}; known: {[d.value for d in LogDriver]}"
        ) from exc

    if not isinstance(config, Mapping):
        raise ObservabilityProfileError(f"log driver {driver!r} config must be a mapping")

    _validate_required_keys(
        driver_label=f"log driver {kind.value}",
        required=_REQUIRED_KEYS_BY_LOG_DRIVER[kind],
        config=config,
    )

    if kind == LogDriver.MULTIPLEXER:
        _validate_multiplexer_children(
            children=config["children"],
            parent_kind="log",
            parse_child=lambda d, c: validate_log_driver_config(
                driver=d,
                config=c,
            ),
        )

    return kind


def validate_metrics_driver_config(
    *,
    driver: str,
    config: Mapping,
) -> MetricsDriver:
    try:
        kind = MetricsDriver(driver)
    except ValueError as exc:
        raise ObservabilityProfileError(
            f"unknown metrics driver {driver!r}; known: {[d.value for d in MetricsDriver]}"
        ) from exc

    if not isinstance(config, Mapping):
        raise ObservabilityProfileError(f"metrics driver {driver!r} config must be a mapping")

    _validate_required_keys(
        driver_label=f"metrics driver {kind.value}",
        required=_REQUIRED_KEYS_BY_METRICS_DRIVER[kind],
        config=config,
    )

    if kind == MetricsDriver.MULTIPLEXER:
        _validate_multiplexer_children(
            children=config["children"],
            parent_kind="metrics",
            parse_child=lambda d, c: validate_metrics_driver_config(
                driver=d,
                config=c,
            ),
        )

    return kind


def validate_trace_driver_config(
    *,
    driver: str,
    config: Mapping,
) -> TraceDriver:
    try:
        kind = TraceDriver(driver)
    except ValueError as exc:
        raise ObservabilityProfileError(
            f"unknown trace driver {driver!r}; known: {[d.value for d in TraceDriver]}"
        ) from exc

    if not isinstance(config, Mapping):
        raise ObservabilityProfileError(f"trace driver {driver!r} config must be a mapping")

    _validate_required_keys(
        driver_label=f"trace driver {kind.value}",
        required=_REQUIRED_KEYS_BY_TRACE_DRIVER[kind],
        config=config,
    )

    if kind == TraceDriver.MULTIPLEXER:
        _validate_multiplexer_children(
            children=config["children"],
            parent_kind="trace",
            parse_child=lambda d, c: validate_trace_driver_config(
                driver=d,
                config=c,
            ),
        )

    return kind


# ---- whole-profile validation --------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ObservabilityProfileSpec:
    """The full payload an org admin submits. Field shape mirrors
    the Django model so create/update can pass through directly."""

    log_driver: str
    log_config: Mapping
    metrics_driver: str
    metrics_config: Mapping
    trace_driver: str
    trace_config: Mapping
    retention: RetentionConfig = dataclasses.field(default_factory=RetentionConfig)
    default_dashboard_id: str = ""


def validate_profile(*, profile: ObservabilityProfileSpec) -> None:
    """Run all per-driver and retention validations. Returns
    nothing — raises on any failure with the SINGLE first issue
    encountered. (Multi-issue accumulation is left to the GraphQL
    layer that maps errors to MutationResult.errors.)
    """
    validate_log_driver_config(
        driver=profile.log_driver,
        config=profile.log_config,
    )
    validate_metrics_driver_config(
        driver=profile.metrics_driver,
        config=profile.metrics_config,
    )
    validate_trace_driver_config(
        driver=profile.trace_driver,
        config=profile.trace_config,
    )
    # RetentionConfig validates in its __post_init__; if we got
    # here it's valid.


def collect_profile_issues(
    *,
    profile: ObservabilityProfileSpec,
) -> tuple[str, ...]:
    """Multi-failure aggregating variant. GraphQL surface uses this
    to populate MutationResult.errors so the operator sees ALL
    misconfigurations in one round-trip, not whack-a-mole."""
    issues: list[str] = []

    for label, fn in (
        (
            "log_driver",
            lambda: validate_log_driver_config(
                driver=profile.log_driver,
                config=profile.log_config,
            ),
        ),
        (
            "metrics_driver",
            lambda: validate_metrics_driver_config(
                driver=profile.metrics_driver,
                config=profile.metrics_config,
            ),
        ),
        (
            "trace_driver",
            lambda: validate_trace_driver_config(
                driver=profile.trace_driver,
                config=profile.trace_config,
            ),
        ),
    ):
        try:
            fn()
        except ObservabilityProfileError as exc:
            issues.append(f"{label}: {exc}")

    return tuple(issues)
