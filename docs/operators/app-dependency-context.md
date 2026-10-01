# Read-only app dependency context

The curated unauthenticated `astroliftServerInfo.capabilities` handshake
advertises `apps.dependency_context` and `providers.reference_read` so clients
can detect these additive contracts. Capabilities describe shipped support,
not authority or observed provider availability. The curated public schema
continues to expose only install discovery; both actual reads use the
authenticated main GraphQL transport. No tenant field or provider catalog data
is added to the public schema, and clients must show refused/unavailable states
instead of substituting a simulated read when support or permission is absent.

`astroliftAppDependencyContext(appId: GUID!, environmentId: GUID!,
expectedClusterId: GUID, expectedProviderId: GUID)` is a read-only projection
for one exact live app/environment pair (#2208). It requires `app.read` on
the actual app, including its live organization/project/team owner chain,
active caller, role/share grants, bearer permission and optional team ceiling,
and the selected environment's authoritative ABAC environment/region facts.
Selecting a different team/project header grants nothing. An environment
belonging to another app, inactive/deleted/foreign cluster, or deleted/disabled
provider supplies no context. Every invocation rechecks authority and live
ownership; the projection has no response cache or provider side effects.

The response carries immutable app, environment, cluster and provider GUIDs.
Retain those identities when navigating. Supply the expected cluster/provider
GUIDs on subsequent reads to refuse an environment reassigned to another
cluster or a cluster reassigned to another provider. A mismatch returns null;
missing or invisible context returns null or a permission error under the
normal scoped gate. A missing context must never retain a prior successful
response as current authority. Slugs describe the current objects, not their
identity.

The permission explanation identifies the required `app.read` gate and reports
effective `app.deploy` and organization `cluster.register` gate decisions at
read time through the same scoped permission engine. These decisions are
advisory. They neither grant actions nor replace fresh mutation authorization,
platform-operator restrictions on shared resources, elevation or other
mutation-specific admission. All existing cluster registration/update/control,
app deployment and certificate mutation permissions remain unchanged.

## Field boundaries and provenance

| Projection                 | Source and meaning                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Cluster/provider           | Exact bound live GUIDs, cluster/provider slugs, region (nullable when unknown), lifecycle and active flag. No endpoint, namespaces, cloud-account identifiers, config, auth material, bootstrap history or infrastructure diagnostics.                                                                                                                                                                 |
| Heartbeat                  | `persisted_cluster_heartbeat`; `heartbeatObservedAt` is the stored receipt timestamp. `AVAILABLE` means a timestamp exists, not that workloads are healthy. `NO_DATA` with `never_seen` and null timestamp means no heartbeat has been observed. Connected/degraded/offline are derived from the actual stored cadence at `readAt`; they are not a new network probe.                                  |
| Selected managed domain    | Only the live environment's domain GUID and zone, confined to the same organization or a shared domain. Deleted/foreign references return `UNAVAILABLE` and null; no reference returns `NO_DATA`. No DNS config, provisioning events, validation challenge or certificate account listing.                                                                                                             |
| Custom domains             | Live domains of the actual app only, explicitly `app_wide_persisted_metadata`. The model has no per-environment binding, so the response makes no such association. At most 100 GUID-ordered domains, with `domainLimit` and `domainsTruncated`; an empty set is `NO_DATA`.                                                                                                                            |
| Certificate observations   | `persisted_custom_domain`; configured-reference presence only, stored certificate state and timestamped cached metadata. Reference contents, PEM, serials, provider errors and validation secrets are excluded. `NO_DATA` has null observation/expiry/renewal fields. A recorded snapshot may be stale; stored expiry/state/renewal metadata is not proof of current binding, renewal or TLS validity. |
| Live provider observations | Always `UNAVAILABLE` with `scoped_live_provider_observations_not_supported`. The existing health API aggregates namespaces and certificate discovery lists whole accounts; this read calls neither. No unreachable, empty, healthy or cost-zero result is invented from an unperformed request.                                                                                                        |

Shared clusters use the same field boundary even for callers with broader
roles. Only the exact app's domains appear; other tenants' namespaces, events,
errors and account-wide certificates are never queried or returned. Cluster
readiness, live workload health and cost are outside this projection and stay
unobserved.

## Provider reference resolution

`astroliftProviderPlugin(slug: String!, expectedId: GUID)` resolves a single live
entry of the existing authenticated install-wide public provider catalog. It
returns exactly the catalog's GUID/slug/name/version/capabilities/isEnabled
shape, without any tenant provider configuration or credentials. It has no
100-row list cap. Supply the previously observed provider GUID to refuse a
deleted/recreated slug. Missing or replaced entries return null. Authentication
is required, exactly as for `astroliftProviderPlugins`; a catalog entry does
not imply authority to register or control any cluster.

The client documents `GET_APP_DEPENDENCY_CONTEXT` and
`GET_PROVIDER_PLUGIN_REFERENCE` and generated SDL/types ship atomically.
Real PostgreSQL and GraphQL HTTP regressions are in
`backend/astrolift_registry/tests/test_dependency_context_2208.py`; they exercise
owner grants, shared/foreign resources, policy and credential refusal,
revocation, immutable identity, field redaction and bounded observations.
