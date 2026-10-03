# Platform activity

`/platform-activity` reads actual Temporal executions. Viewing the list and
history requires `audit_log.read`; the server narrows results to the caller's
organization and granted app/project scopes. Cancel and terminate require
`workflow.trigger` at the target's own scope, or the existing elevated platform
operator authority. Visible controls do not replace the server permission check.

Type and Status filter on the server. Older and Newer navigate Temporal's opaque
cursor pages. An authorization-filtered page can contain no visible rows while
Older remains available; continue paging rather than interpreting that page as
an idle engine. The API does not provide global search, arbitrary sorting or an
exact total, so the viewer does not offer those controls. The list polls every
15 seconds while the browser tab is visible and supports manual refresh.

Instance links include both `instance` (workflow ID) and `instanceRun` (Temporal
run ID). Detail/history reads and confirmed actions use that exact pair. A
replacement with the same workflow ID remains a different execution. Old links
without an execution ID must be closed and reopened from the list; the viewer
never resolves them to the newest execution. Changing or closing the selection
abandons an outstanding confirmation.

The GraphQL `astroliftWorkflowInstanceDetail`, `cancelWorkflowInstance` and
`terminateWorkflowInstance` fields accept `runId`. Existing API callers that omit
it retain their legacy latest-execution behavior; consumers that review an
execution should always supply it. An explicitly empty value is refused.

Cancel acknowledges a cooperative cancellation request, not completed cleanup.
Terminate is a hard stop and does not run cleanup; it requires a reason. A lost
reply or permission/network failure is not proof that no action occurred. Refresh
the same execution, check current access/status, and then decide whether to retry.
The viewer displays a failure without reproducing the transport error payload.
History remains bounded to the API's first 200 shaped events; this viewer is not
a complete-history export.
