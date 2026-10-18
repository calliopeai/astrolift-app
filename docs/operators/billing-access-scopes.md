# Quota access scopes

The quota console belongs to the active organization. `astroliftQuotas`,
`astroliftQuotaUsageHistory` and `requestQuotaIncrease` require `billing.read`
at that explicit organization scope. The legacy permission name and
`org_billing` role remain compatible with existing grants; a custom
organization role may supply the permission as well. Team and project grants
do not grant upward, and selecting a team or project does not change the
quota console's owner.

Quota `scope_kind`/`scope_id` identifies the resource allocation. It does not
turn the organization console into a team-owned collection. An organization
grant reads the organization's allocations across teams; organization filters
exclude other organizations' rows. Foreign, deleted or unknown quota IDs
return empty history or `NOT_FOUND` for an increase request after organization
authorization.

Bearer tokens require both the owner's organization permission and the token
permission ceiling. These quota APIs require `admin` on the token;
`read:apps` and the default CLI device-token scopes do not cover them. A team-bound
token cannot use organization quota APIs even if its owner has an organization
role or is a platform operator. An organization token cannot reuse a role in
another selected organization. Use an organization-bound token with `admin`
for authorized quota automation.

Quota increase requests remain requests, requiring `billing.read` rather
than `billing.update`. Permission refusals occur before request creation,
notifications and email; authorized requests retain their validation,
pending-request conflict and notification behavior. The resource quota usage
snapshot collector remains separate from retired monetary collection.

Monetary budget, snapshot, trend, forecast and attribution APIs are removed.
Historical budget and cost snapshot models remain installed with read-only
admin access. Retained cost workflow/activity names support existing Temporal
histories through no-op activities; they do not collect costs. Existing cost
schedules must be paused and old workers drained by the operator. See
[the coordinated client/API and worker retirement procedure](retire-costing.md).
This release changes the GraphQL contract and its generated clients; it does
not drop or rewrite historical monetary data.

`backend/astrolift_billing/tests/test_org_scopes_2113.py` covers the retained
quota entry points with PostgreSQL RoleBindings, sibling teams,
cross-organization rows and session/bearer HTTP requests. Historical budget
and snapshot fixtures in those tests do not establish active monetary APIs.
