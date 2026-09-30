# Form access scopes

Form definitions have an organization owner. Submissions carry the same
organization and belong to a form; there is no team/project/app owner field
on either model. Reading form definitions or responses requires `form.read`
at the active organization. Creation, editing/publishing/archiving,
deletion and moderation require `form.create`, `form.update`, `form.delete`
and `form.moderate` respectively at that same explicit organization scope.
Selecting a team or project does not grant authority over organization forms.

The definition collection retains the organization's live forms, and the
submission collection retains the named form's live responses. Foreign,
deleted and unknown targets keep their existing null/empty or `NOT_FOUND`
response after authorization. Moderation also requires the submission's
form to be live and owned by the active organization, so inconsistent
historical owner links cannot expose or change another organization's form.

Bearer calls require the user's organization role and the token's permission
ceiling. The existing form permissions require `admin` on API tokens;
`read:apps` and CLI device scopes do not cover them. Team-bound bearers cannot
use organization form management, response reads or private submission,
even when their owner holds an organization role or is a platform operator.
Organization-bound credentials cannot reuse a role against a different
selected organization.

`formSubmissionReceived` requires organization `form.read`. WebSocket
identity and bearer ceilings are pinned before the gate runs at first
iteration. A refused or missing-tenant stream completes silently, without
polling or emitting an event. An authorized stream resolves one live form
in the organization, retains its ID, and polls only responses whose form
and submission owners match that organization. Deleted forms and a new
form reusing a deleted slug do not enter an existing stream. Cancellation
and explicit close unwind the inner polling generator.

Published public forms retain their submission exception: an anonymous or
nonmember submitter may send a response without `form.submit`. That exception
does not authorize reading responses or subscribing to new submissions.
Private forms require organization `form.submit`, and all submission paths
retain published-state and payload validation.

The API fields and database schema are unchanged. No migration or frontend
regeneration is required. Real PostgreSQL tests in
`backend/astrolift_forms/tests/test_org_scopes_2112.py` and
`test_subscription_scopes_2112.py` cover two-team RoleBindings, cross-org
rows, session/bearer HTTP requests, silent stream refusal, event filtering
and cancellation. The surface guardrail has no `#2112` exemptions.
