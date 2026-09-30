# Dashboard query freshness

Dashboard writes refresh active Apollo reads by operation name when the list's
cursor, search or filters determine its variables. Add team, member grants,
invitations, SSH deploy keys and source Client IDs therefore refresh the page
already on screen. Notification read actions refresh the loaded inbox window,
including an expanded window, instead of issuing a query with default arguments.
A rejected write reports an error; a committed write whose refresh fails retains
its success and reports the refresh failure separately.

The app frame owns the shared 15-second recent-deployment poll on every app tab.
The Overview, approval queue and deploy strip consume that cache. Deploying from
an environment control refreshes its latest deployment as well, so the next
blank-tag deployment uses the new tag. The cluster Status summaries poll recent
workflows every 15 seconds and lifecycle events every 30 seconds, matching the
Activity tab. Alert mute labels tick locally once per second; changing a workflow
instance's free-text Type filter waits 300 milliseconds before requesting data.

## Scope before limits

Task-home reads send both the app and workload to the server before taking the
newest thirty runs. Deployment details request events for their own app before
taking the recent summary. Security reads request the latest signing, SBOM and
scan event independently by app and event type, so unrelated events cannot hide
an older security fact.

The Observability card reads every cursor page in its fourteen-day deployment
window, using started time and falling back to creation time for a deployment
that has not started. It retains loading until all pages arrive and reports a
continuation failure instead of presenting a partial chart as complete. Its app
alert counts are server aggregates over every visible unresolved firing, including
an exact critical count. The alert links retain the app constraint in the rules
and firing-history reads.

The additive operations fields are:

| Field | Result |
| --- | --- |
| `astroliftEvent(id)` | One visible platform event, or null |
| `astroliftAlertRule(id)` | One visible rule, including an inactive rule, or null |
| `astroliftAlertEvent(id)` | One visible firing, including a resolved firing, or null |
| `astroliftAlertEventSummary(appSlug)` | Exact unresolved and critical counts |

Alert rule/event list fields and their cursor companions accept optional
`appSlug`. The app filter covers app, environment and workload targets and live
private services belonging to that app. An ambiguous environment/workload name
cannot identify a particular app; a GUID can. Unbound global and shared project-resource
rules do not count as an individual app's rules. Existing owner visibility,
bearer ceilings and persisted environment policies apply before direct reads,
counts and cursor paging. Missing or invisible details return null; a missing
permission fails the read. A transport or GraphQL failure renders a retryable
error in the detail frame instead of a not-found or empty state.

## Existing migrated surfaces

The #2143 audit also verified the current contracts already replacing the older
reported implementations: cluster detail uses `GetCluster`; tool detail uses
`toolDef(id)`; agent Observe sends the workload ID to `agentTasksPage`; app
deployments and Functions filter and count on the server; the Fleet overview
shares its three reads across summaries; workflow run history polls while the
run is live; and Preview server rendering preloads the cursor-page document the
list reads. Agent discovery's effective-ref scan key and stale-response guards
are covered by #2147.
