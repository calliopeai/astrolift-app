"""Astrolift observability — SRE golden-signals surface (#380).

This module wraps the cluster-side Prometheus stack (shipped by the
platform-bootstrap recipe) and exposes the four golden signals
(traffic, errors, latency, saturation) plus a per-HTTP-status-code
breakdown as a typed GraphQL surface for the App > Observability tab.

It deliberately reuses the existing Prometheus transport in
``astrolift_operations.prometheus_client`` rather than introducing a
new async HTTP dependency — the wire protocol is identical and the
existing client already handles cache, 4xx/5xx mapping, and label-
value sanitization.

Layout:

* ``prom_queries`` — pure PromQL builders + ``QueryPlan`` shapes,
  no I/O.
* ``prom_client`` — thin adapter over the operations Prometheus
  client; converts ``RangeQueryResult`` rows into
  ``(timestamp, value)`` series and groups by status-code class.
* ``schema/`` — Strawberry types + resolvers.
"""

default_app_config = "astrolift_observability.apps.AstroliftObservabilityConfig"
