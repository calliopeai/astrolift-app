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
