# Scoped logs and trace explorers (#2260)

The `/logs` and `/traces` pages require an authenticated account, selected organization,
app, and ordinary environment. App choices use the server cursor list; environment
choices use its numbered pages. Preview logs retain their separate four-part reviewed
identity contract and are opened from the app's Previews page. These explorers do not
install collectors or imply that telemetry exists.

The app's persisted `AppEnvironment` foreign key selects the live cluster and namespace.
A shared platform cluster with `organization_id=null` is supported only through that
explicit placement. A foreign organization-owned, retired, inactive, or replaced target
is refused before dispatch. Owner ancestry and the placement receipt are checked again
before releasing results. The UI sends the immutable environment GUID as well as its
name; a deleted/recreated name cannot silently select a replacement.

## Historical logs and live navigation

`astroliftAppLogs` retains its existing filters, opaque cursor, retention indicator,
`historicalAvailable`, and `APP_READ_LOGS` permission. Its additive `environmentId`
selects an exact ordinary environment; preview requests still require all existing
preview proof arguments. The additive `scope` describes the persisted admitted placement.
The UI offers message/level filters, window selection, refresh, and older server pages.
The live-log link opens the existing app observability pods panel with the selected
environment; a historical collector is not required for that existing live path.

Configure a supported historical driver on `TenantCluster.provider_config`, for example
`log_driver: loki` with `log_config.endpoint`. Loki must ingest labels matching the
platform's namespace/app selector contract. Transport configuration and authentication
remain operator controlled. A zero-result query does not establish that ingestion or
retention is configured correctly. This change adds no collector deployment (#1706).

For CloudWatch, set `log_driver: cloudwatch_logs` and `log_config.log_group` plus
`log_config.region`. The reader uses the registered cluster's `credential` declaration,
including its assumed role and external ID; that role needs `logs:FilterLogEvents`
on the exact group ARN. Without an explicit declaration it uses the control plane's
ambient identity. Legacy `log_config.role_arn` remains supported on ambient registrations.
Do not combine it with an explicit cluster credential: remove the legacy override
before enabling the registered identity. Conflicting declarations and failed role
assumption are refused, with no ambient fallback. A collector's write-only IRSA role
is not a reader role.

## Trusted trace attribution

Configure `trace_driver: tempo` and `trace_config.endpoint` only after the collector is
verified. Set `trace_config.attribution: collector-resource-v1` to acknowledge the
following operator prerequisite. The marker is not a verifier and does not itself make
an arbitrary exporter trusted. The trusted ingestion path must remove caller-supplied
ownership attributes and stamp authoritative workload identity on each span's resource:

| Resource attribute | Authoritative value |
| --- | --- |
| `astrolift.organization.id` | Persisted app organization's GUID |
| `astrolift.app.id` | Persisted app GUID |
| `astrolift.environment.id` | Persisted environment GUID |
| `astrolift.cluster.id` | Persisted placement cluster GUID |
| `k8s.namespace.name` | Persisted effective environment namespace |

Do not derive these attributes from an untrusted request, service name, user-entered
filter, or arbitrary exporter metadata. A shared collector must maintain this mapping
per workload and remove spoofed ownership attributes before stamping. Preserve ordinary
resource fields such as `service.name` independently of span attributes. Operator-only
Tempo authentication (`bearer_token`, optional `org_id`) is transport authentication;
it does not replace resource attribution.

`astroliftAppTracePage` requires `APP_READ`, an app, an optional exact environment
GUID/name, and ordered `since`/`until` strings (Unix seconds or timezone-aware ISO;
legacy `now`/`now-1h` forms remain bounded). Windows are at most 24 hours. Service/status
filters are server-side. At most 20 candidates are returned (UI requests 10); the extra
candidate only indicates `truncated`. Tempo supplies first-match search results, not
verified latest-first ordering or cursor pagination. Narrow the time window or service
filter to refine this bounded sample. The small bounded sample uses the shared ListPage/DataTable
with explicitly bounded presentation, without inventing client pagination or a raw-table exception.

`astroliftTraceSpansResult` additionally requires a 32-character hexadecimal trace ID.
It first performs a resource-scoped TraceQL search with `trace:id`; it never fetches an
arbitrary cluster-wide trace ID without a matching scoped search. Both paths verify all
five resource attributes on every returned span. Span-level claims do not authorize a
read. Foreign spans, foreign summary/root metadata, and parent references outside the
owned subset are removed. Trace summaries are calculated from that subset. The SDK
preserves resource and span attributes separately, accepts Tempo JSON OTLP batches,
and bounds response bodies to 8 MiB and traces to 2,000 spans.

Use a Tempo version supporting quoted resource keys and the `trace:id`, `span:name`,
`span:duration`, and `span:status` intrinsics documented in the
[official TraceQL guide](https://grafana.com/docs/tempo/latest/traceql/construct-traceql-queries/).
Native local HTTP fixtures prove request construction and response admission, not live
Tempo query-engine acceptance or actual fleet ingestion. Operators must verify their
supported engine and trusted collector mapping before enabling attribution.

## Truthful read states and discovery

| Envelope | Meaning |
| --- | --- |
| `NOT_CONFIGURED`, `scope: null` | Selected app/environment placement unavailable or changed; reselect/check placement |
| `NOT_CONFIGURED`, scope present | Admitted placement has no supported configured collector/backend (traces also require trusted attribution) |
| `NO_DATA_YET` | Successful scoped read returned no admitted data in this window |
| `ERROR` | Backend failed or candidates could not be safely attributed; do not present as empty success |
| `OK` | Admitted data for the returned scope |

The pages expose configuration guidance and retry controls; raw inherited GraphQL
error diagnostics remain visible. Provider response bodies and foreign telemetry are
not logged by these explorer resolvers. In-flight UI results are bound to account,
organization, immutable app/environment/cluster identities and the query window.
Changing that scope hides prior data immediately.

Server discovery advertises `observability.exact_environment_logs` and
`observability.scoped_trace_envelopes` through `astroliftServerInfo` (and existing
`astro capabilities` / `astro doctor` discovery). These indicate available API contracts,
not configured collectors. Existing legacy trace array fields retain their shape and
permissions but now use the same guarded attribution path. No new CLI trace command,
cluster-wide search, export, service map, automatic tracing setup, or completed live
collector verification is claimed by this batch.
