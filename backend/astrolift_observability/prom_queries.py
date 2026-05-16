"""Pure PromQL builders for the App > Observability golden-signals
surface (#380).

Each builder takes the app slug + environment name + window width and
returns a :class:`QueryPlan` carrying:

* ``promql`` — the rendered PromQL expression. Dev-mode disclosure in
  the UI surfaces this verbatim so operators can paste into Grafana.
* ``labels`` — the dict of PromQL label matchers actually used. Kept
  separate from ``promql`` so the resolver / tests can assert label
  shape without re-parsing the string.

The builders are pure: no HTTP, no Django, no globals. They run the
same label-value sanitizer the existing operations Prometheus path
runs (``astrolift_operations.prometheus_client.sanitize_label_value``)
so a rogue caller can't break out of the label-match quoting.

PromQL contract (matches the operator-facing copy in the issue):

* request rate (traffic): ``sum(rate(http_requests_total{...}[<w>]))``
* error rate (errors):    ``sum(rate(http_requests_total{...,code=~"5.."}[<w>])) / clamp_min(sum(rate(http_requests_total{...}[<w>])), 1e-9)``
* latency p50/p90/p99:    ``histogram_quantile(q, sum by (le)(rate(http_request_duration_seconds_bucket{...}[<w>])))``
* cpu saturation:         ``sum(rate(container_cpu_usage_seconds_total{...}[<w>])) / sum(kube_pod_container_resource_limits{resource="cpu",...})``
* status-code breakdown:  ``sum by (code) (rate(http_requests_total{...}[<w>]))``

The rate window (``<w>``) widens with the time-range so short ranges
are snappy and long ranges stay stable. The rule lives in
:func:`pick_rate_window` so callers and tests share it.
"""

from __future__ import annotations

import dataclasses

from astrolift_operations.prometheus_client import sanitize_label_value

# ----------------------------------------------------------------------
# QueryPlan
# ----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class QueryPlan:
    """Pure-data PromQL bundle.

    The resolver passes ``promql`` straight to the Prometheus HTTP
    API; tests assert against both the rendered string and the
    ``labels`` map so a refactor can change quoting / ordering without
    silently breaking the matcher contract.
    """

    promql: str
    labels: dict[str, str]
    rate_window: str
    """The rate-of-change window used inside the PromQL — exposed so
    range-query callers can pass the same width as the ``step`` lower
    bound and keep the series honest."""


# ----------------------------------------------------------------------
# rate-window selection
# ----------------------------------------------------------------------


def pick_rate_window(range_seconds: int) -> str:
    """Pick the ``rate(...[<w>])`` window for ``range_seconds``.

    The mapping is:

    * <= 1h   → ``1m``  (snappy on the live tab)
    * <= 6h   → ``5m``  (smooths sub-minute jitter)
    * <= 24h  → ``5m``
    * <= 7d   → ``15m`` (longer windows need wider smoothing)
    * else    → ``1h``

    Callers (resolver + range-query step calc) share this rule so the
    PromQL window and the request-range step always agree.
    """
    if range_seconds <= 60 * 60:
        return "1m"
    if range_seconds <= 24 * 60 * 60:
        return "5m"
    if range_seconds <= 7 * 24 * 60 * 60:
        return "15m"
    return "1h"


def pick_step_seconds(range_seconds: int, *, max_points: int = 360) -> int:
    """Pick a ``step`` for ``query_range`` so the returned matrix has
    at most ``max_points`` samples.

    Recharts struggles past a few hundred points on the wire; 360 is
    the standard ceiling we use across the metrics surface (one point
    per minute over six hours, one per ~four-minutes over a day, one
    per ~half-hour over a week).
    """
    if range_seconds <= 0:
        raise ValueError("range_seconds must be positive")
    step = range_seconds // max_points
    if step < 15:
        # Prometheus ``step`` < 15s is rarely useful and burns CPU; clamp.
        return 15
    return int(step)


# ----------------------------------------------------------------------
# label-matcher rendering
# ----------------------------------------------------------------------


def _build_labels(
    *,
    app_slug: str,
    environment_name: str | None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the label-matcher dict for ``app_slug`` (+ env).

    Both inputs are sanitized via the same allow-list the operations
    Prometheus client uses — a caller can't smuggle in a quote or a
    backslash that would break out of the PromQL label match.
    """
    labels: dict[str, str] = {"app": sanitize_label_value(app_slug)}
    if environment_name:
        labels["environment"] = sanitize_label_value(environment_name)
    if extra:
        for k, v in extra.items():
            # The label *value* is sanitized; the *key* must be a
            # bare PromQL identifier — caller's responsibility.
            labels[k] = sanitize_label_value(v)
    return labels


def _render_label_match(labels: dict[str, str]) -> str:
    """Render ``{a="x",b="y"}`` with deterministic key order."""
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


def _render_label_match_with_extra(
    labels: dict[str, str],
    extra: str | None,
) -> str:
    """Render ``{a="x",<extra>}`` — used for inline regex matchers
    (e.g. ``code=~"5.."``) that don't fit the plain ``=`` map.

    ``extra`` is appended verbatim inside the braces; the caller is
    responsible for sanitizing anything user-controlled in it.
    """
    base = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    if extra:
        return "{" + base + "," + extra + "}"
    return "{" + base + "}"


# ----------------------------------------------------------------------
# builders
# ----------------------------------------------------------------------


def build_request_rate_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
) -> QueryPlan:
    """Traffic — requests / second.

    ``sum(rate(http_requests_total{app=...}[<w>]))``
    """
    labels = _build_labels(app_slug=app_slug, environment_name=environment_name)
    rate_window = pick_rate_window(range_seconds)
    expr = f"sum(rate(http_requests_total{_render_label_match(labels)}[{rate_window}]))"
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_error_rate_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
) -> QueryPlan:
    """Errors — 5xx rate / total rate.

    ``sum(rate(http_requests_total{...,code=~"5.."}[<w>])) / clamp_min(sum(rate(http_requests_total{...}[<w>])), 1e-9)``

    ``clamp_min`` guards the zero-traffic case so the resolver gets
    ``0`` instead of a NaN (Prometheus serializes NaN as the string
    ``"NaN"`` which the existing client coerces to ``0.0`` but only
    after a parse failure — cleaner to clamp at the query layer).
    """
    labels = _build_labels(app_slug=app_slug, environment_name=environment_name)
    rate_window = pick_rate_window(range_seconds)
    base_match = _render_label_match(labels)
    err_match = _render_label_match_with_extra(labels, 'code=~"5.."')
    expr = (
        f"sum(rate(http_requests_total{err_match}[{rate_window}])) "
        f"/ clamp_min(sum(rate(http_requests_total{base_match}[{rate_window}])), 1e-9)"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_latency_quantile_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    quantile: float,
) -> QueryPlan:
    """Latency — histogram_quantile over the request-latency bucket
    histogram.

    ``histogram_quantile(q, sum by (le)(rate(http_request_duration_seconds_bucket{...}[<w>])))``
    """
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile must be in (0, 1); got {quantile}")
    labels = _build_labels(app_slug=app_slug, environment_name=environment_name)
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = (
        f"histogram_quantile({quantile:g}, "
        f"sum by (le)(rate(http_request_duration_seconds_bucket{match}[{rate_window}])))"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_cpu_saturation_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
) -> QueryPlan:
    """Saturation — actual CPU vs. requested limit.

    ``sum(rate(container_cpu_usage_seconds_total{app=...}[<w>])) / sum(kube_pod_container_resource_limits{app=...,resource="cpu"})``

    Series is a ratio in [0, 1+] (>1 = over-limit / throttling). The
    UI shells this into a percent. ``kube_pod_container_resource_limits``
    comes from kube-state-metrics which the bootstrap recipe already
    installs.
    """
    labels = _build_labels(app_slug=app_slug, environment_name=environment_name)
    rate_window = pick_rate_window(range_seconds)
    usage_match = _render_label_match(labels)
    limits_match = _render_label_match_with_extra(labels, 'resource="cpu"')
    expr = (
        f"sum(rate(container_cpu_usage_seconds_total{usage_match}[{rate_window}])) "
        f"/ clamp_min(sum(kube_pod_container_resource_limits{limits_match}), 1e-9)"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_status_code_breakdown_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
) -> QueryPlan:
    """Per-status-code stacked time-series.

    ``sum by (code) (rate(http_requests_total{...}[<w>]))``

    The resolver groups the returned matrix into 2xx / 3xx / 4xx / 5xx
    classes plus a top-5 individual-code list for tooltip use.
    """
    labels = _build_labels(app_slug=app_slug, environment_name=environment_name)
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = f"sum by (code) (rate(http_requests_total{match}[{rate_window}]))"
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)
