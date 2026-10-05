# Cluster and domain access scopes

Read-only app operators can inspect a bounded environment dependency projection
with `app.read`, without obtaining cluster mutation authority. See
[read-only dependency context](app-dependency-context.md) for its exact GUID,
redaction and persisted-observation contract. Organization-level inventory and
control routes below retain their existing gates.

Tenant clusters, managed domains and per-organization provider configuration
belong to an organization. Their inventory, detail, live state, bootstrap,
metrics, lifecycle, identity-user and configuration routes require the declared
permission at that explicit organization scope. Team/project/app grants do not
grant upward. Selecting a team or project in a browser request does not change
an organization's ownership or the authority needed to operate it.

A missing, deleted or foreign target resolves to an explicit organization
scope, then the handler's organization filter returns its normal missing-target
response. Scope factories never fall back to the selected team. Cluster/domain
collections contain the active organization's rows plus shared rows; they have
no team-owned subset to expose through an any-scope grant.

Bearer tokens retain both ceilings: their permission scopes and their optional
team boundary. A team-bound token cannot use these organization routes even
when its owner has an organization role or is a platform operator and the token
has `admin`. An organization-bound token still needs the declared permission
scope and the user's RoleBinding. The authenticated provider-plugin catalogue
continues to describe capabilities without configuration or credentials.

Shared cluster/domain/provider-configuration writes require the active platform
operator and bearer `admin` scope. This includes recording a shared cluster's
bootstrap history and domain provisioning/revalidation/certificate reissue
when either the cluster or the domain is shared. An owned cluster cannot act as
a vehicle for changing a shared domain.

Shared reads retain their existing limits: inventory and tenant-owned app
namespace observations remain available to an organization grant; install-wide
cloud DNS/certificate discovery, shared central-auth users, cluster-wide metrics
and shared workflow history remain restricted or return an unsupported/empty
view to tenants. An organization administrator has no install-wide authority.

`backend/astrolift_clusters/tests/test_org_scopes_2108.py` exercises all 48 routes
with real PostgreSQL RoleBindings in the shared two-team world, including
selected sibling teams, project/foreign-org grants and bearer ceilings. The
surface guardrail has no `#2108` gap exemptions. API fields and database schema
are unchanged.

## Managed-domain diagnostics

[Read-only domain diagnostics](managed-domain-diagnostics.md) require current
organization provider-read authority. Shared-domain diagnostics and install-wide
Route53 inventory additionally require the current platform operator.
Registered-zone boundaries, fresh source checks and pre-network throttles do not
expand existing DNS/certificate mutation authority.

## Unregistration review and feedback

The list/detail unregister flow retains the exact `unregisterTenantCluster`
input `{ id }`, CLUSTER_UNREGISTER organization/bearer checks and shared-cluster
operator fence. The server refuses while active apps target the cluster or live
cluster-owned shared model deployments remain. Move apps and deprovision shared
models first. This mutation retires control-plane registration; it does not
claim infrastructure decommissioning, physical credential erasure, or removal
of retained records. Decommissioning remains a separate operation.

Refusals retain the reviewed target and original diagnostic without triggering
list/detail/legacy inventory reads. Only accepted envelopes refresh those
existing queries with their original variables. Failed refreshes warn about the
read without changing the accepted outcome. A detail-page navigation exception
likewise reports acceptance and suggests opening the cluster list manually.
An old accepted reply cannot navigate a newer route or close a newer review.

Local reviews use the current visible GUID/slug/name/organization/provider/
region/lifecycle/active snapshot and existing permission visibility. Withdrawn
rows or authority, changed list context, observed replacements and A→B→A source
transitions invalidate old callbacks. Cached same-target observations retain
their current read behavior; changing locale alone preserves the review.
These checks add no server immutable-incarnation/version precondition and do
not establish identity or infrastructure state beyond the actual response.
