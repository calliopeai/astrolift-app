# Policy-governed app connections to hosted models

Model creation and model-level connection restrictions remain platform-superadmin
operations with the existing organization and cluster hosting admission. App owners
connect an already admitted, ready model through their current `app.update` grant.
Organization policy administration uses current `org.update` in the selected
organization; an app owner cannot change that policy or a model restriction.

## Admission and configuration

`MODEL_CONNECTION_DEFAULT_MODE` is `AUTO` by default, preserving the existing
automatic subscription path. `MODEL_CONNECTION_DEFAULT_QUORUM` defaults to one
and `MODEL_CONNECTION_ALLOW_SELF_APPROVAL` defaults to false. An organization can
set an explicit policy with `updateOrganizationModelConnectionPolicy`, using its
current policy version (zero for the installation default). Supported modes are
`AUTO`, `REQUIRE_APPROVAL` and `DENY`, with one through sixteen distinct reviewers.

`modelConnectionRestriction` reads the exact selected model/cluster/provider
overlay under fresh platform-superadmin and hosting authority. An absent model
row returns `id: null`, `version: 0`, `AUTO`, quorum one and self-approval allowed:
this is the neutral overlay, not the installation or organization policy. Read
its current version before `setModelConnectionRestriction`, then reload after
a successful edit.

`setModelConnectionRestriction` can tighten an organization's mode, increase its
quorum or prohibit self-approval. It cannot loosen the effective organization
policy. Unknown installation settings refuse connections. Current RBAC, tenant
ownership, bearer organization/team/scope ceilings and ABAC denial still apply,
including for platform operators. This policy governs new intake and finalization;
it does not automatically revoke already connected subscriptions. Existing SHARED/DEDICATED model access and live
source/placement admission remain mandatory.

The discovery marker `models.connection_approvals` advertises this typed API; it
is not a permission or a readiness result. `modelConnectionTargetsPage` takes the
exact organization/model/cluster/provider tuple and returns destination versions,
current `AUTO`, `REQUEST` or `DENY` action, and the effective policy fingerprint.
`modelConnectionAction` rechecks a selected environment. `AUTO` requires the legacy
direct-subscription `org.read` and `app.update` admission. `REQUEST` needs only its
scoped `app.update` admission, without an additional app-read/organization-read
grant. Rows denied by policy or placement are never eligible.

A well-formed, unsatisfied ABAC `approval_required` condition on an ALLOW policy
may defer only for this metadata/intake gate. Actual approvals remain zero.
DENY policies, unknown facts, malformed conditions and all other unsatisfied
conditions refuse. The request-only context ends before resolver effects; ordinary
subscription admission does not inherit it. No caller supplies an approval count. Review votes use ordinary
`app.approve_deploy` admission and do not defer that action’s own approval policy.

## Request, review and connect

1. `requestModelConnection` submits the exact selected tuple, model/app/environment
   versions, policy fingerprint, alias and caller-generated idempotency GUID.
   It persists a tenant-owned PENDING request only. Replaying the same reviewed
   request returns its identity; reusing the key for another target is refused.
   Intake creates no subscription, credential reference, network binding or workflow.
2. A reviewer needs current `org.update` and scoped `app.approve_deploy`, a live
   actor/membership and a valid current credential. `modelConnectionApprovalRequestsPage`
   and `modelConnectionReviewRequest` provide the review inbox without requiring
   the reviewer to hold `app.update`. `approveModelConnectionRequest` records one
   durable vote per distinct user; repeating a vote never increases quorum.
   `rejectModelConnectionRequest` closes a request. Self-review is refused unless
   explicitly permitted by the effective policy.
3. APPROVED grants a reviewed connection opportunity; it does **not** connect the
   app. The current requester selects **Connect**, invoking
   `finalizeModelConnectionRequest` with the current request version. Finalization
   rechecks the requester and the currently eligible durable voters, then calls
   the existing locked subscription machinery with the actual human count.
   A successful repeat returns the same subscription, without another workflow.
   The resulting subscription is pending reconciliation, not runtime-ready.
4. `cancelModelConnectionRequest` lets the current requester close an unfinalized
   pending or approved request. Closed requests cannot connect. Policy or reviewed
   tuple/source changes mark pending/approved requests STALE when they are rechecked;
   unavailable or unauthorized targets may instead refuse without revealing them.
   Start a new reviewed request after such changes.

The public DTO contains safe GUIDs, bounded live model/app/environment labels,
a non-email requester username when available, versions, status, vote count and action hints,
never credential IDs/material or network capabilities. `approvalCount` reports
currently qualified distinct live votes; finalization requalifies them again.
Action hints are advisory and never replace the mutation's fresh target check.

## Concurrency and limits

Decisions serialize on the live organization, then lock model, cluster/provider, app,
environment and request in that order. Fresh current credential/actor/membership/RBAC/ABAC
checks run after target lock waits. The final attachment checkpoint also reloads
the reviewed policy/source and currently eligible human quorum after its last
blocking row lock, before subscription or reconciliation effects. Browser admission reads the current persisted
Django session through the shared read-only validator and validates expiry, actor
and auth hash; revoked, deleted, expired or foreign-actor tracked sessions refuse.
A request-local refusal marker prevents response tracking from reviving the
withdrawn sidecar. The ordinary automatic connection and subscription-revocation
paths apply the same persisted-session validation after their final attachment
lock wait, with authentication facts rebuilt from the fresh session bag.
Bearer credential ceilings and direct non-HTTP service admission remain unchanged. This is a fresh admission check, not a global atomic snapshot of
all authority rows or a logout/response-persistence linearization guarantee.

Votes retain an internal link to the review credential so expired/revoked tokens
or invalid sessions, withdrawn memberships/grants and changed policies cannot
serve as a current quorum. Freshness/MFA facts that cannot be reconstructed for
that credential refuse; stored votes do not fabricate new authentication proof.
Requests and votes are durable, with soft deletion and standard tracking. The
existing model mutation audit boundary applies; this feature does not add a
strict audit-persistence guarantee, outbound notifications, token usage/cost,
provider account discovery or a new approval framework.
