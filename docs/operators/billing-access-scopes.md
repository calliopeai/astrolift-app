# Billing access scopes

The cost and quota consoles belong to the active organization. All eight
GraphQL billing entry points require `billing.read` at that explicit
organization scope: quotas, quota usage history, budgets, cost snapshots,
cost trend, cost forecast, cost attribution and quota increase requests.
The `org_billing` role supplies this permission; a custom organization role
may supply it as well. Team and project grants do not grant upward, and
selecting a team or project does not change the billing owner.

Quota and budget `scope_kind`/`scope_id` identify the resource allocation,
and cost project/app/service fields identify attribution. These fields do
not turn the existing organization consoles into team-owned collections.
An organization billing grant reads the organization's allocations across
teams, including unattributed spend; the existing organization filters keep
other organizations' rows out of lists, totals and forecasts. Foreign,
deleted or unknown quota IDs keep the existing empty-history or
`NOT_FOUND` request response after organization authorization.

Bearer tokens require both the owner's organization permission and the token
permission ceiling. Billing requires `admin` on the token; `read:apps` and
the CLI device token scopes do not cover it. A team-bound token cannot use
organization billing even if its owner has an organization role or is a
platform operator. An organization token cannot reuse a role in another
selected organization. Use an organization-bound token with `admin` for
authorized billing automation.

Quota increase requests remain requests, requiring `billing.read` rather
than `billing.update`. Permission refusals occur before request creation,
notifications and email; authorized requests keep their validation,
pending-request conflict and notification behavior. Scheduled cost and
usage collectors retain their existing machine execution path.

The API fields and database schema are unchanged. Roll out the backend
normally; no migration or frontend regeneration is required.
`backend/astrolift_billing/tests/test_org_scopes_2113.py` verifies all eight
entry points with real PostgreSQL RoleBindings, two sibling teams,
cross-organization rows, and session/bearer HTTP requests. The surface
guardrail has no `#2113` gap exemptions.
