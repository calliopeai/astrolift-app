# Dispatch clients

The CLI, console and agent clients use Astrolift's existing Dispatch lifecycle.
The controller schedules work, receives runtime status and records outcomes.
A chat-based overseer can use the same API as another authenticated client.

## Managed IDE runtime authority

`POST /api/dispatch/v1/boxes/<box-guid>/runtime/validate/` is an internal runtime
endpoint. It accepts only an expiring, box-scoped `Bearer alft_box_...` credential,
not a user session or ordinary CLI token. Only its SHA-256 hash is persisted.
The JSON body, bounded to 4 KiB, is
`{ version: 1, ownerEpoch, podUid, runtimeInstanceId }`; the runtime instance is
the current server process's 32-character lowercase hex nonce.

Successful `MutationResult.data` contains:

```json
{
  "version": 1,
  "owner": {
    "apiUrl": "https://platform.example.test",
    "organizationId": "organization UUID",
    "boxId": "box UUID",
    "ownerEpoch": "stable owner UUID",
    "workspacePath": "/workspace"
  },
  "incarnation": {
    "namespace": "managed",
    "podName": "managed-pod",
    "podUid": "Kubernetes pod UID",
    "containerName": "ide",
    "runtimeInstanceId": "32 lowercase hex characters"
  }
}
```

The owner address comes from the control plane, including its configured HTTPS
`PLATFORM_API_URL`. CLI server aliases are local transport configuration and do
not enter that address. The response proves resource ownership at validation
time; it does not prove extension readiness or grant a task transfer claim.
The receiver must independently require that the returned incarnation names its
own process and revalidate before transfer admission, acknowledgement and resume.

The internal `certify_managed_runtime` provisioner helper freezes the owner epoch,
cluster, Job/Pod/PVC UIDs, container and immutable image, and returns a credential
once with a one-hour expiry. The helper and endpoint both read live resources;
the primary container must report the configured image as running and a stable
immutable observed `imageID` (which may differ from a multi-platform index digest).
Missing or changed observations are refused; `docker-pullable://` is normalized.
The primary container must have its expected writable claim mounted
at `/state` and the workspace, with distinct `state` and `workspace` subpaths.
The first validation binds a process nonce atomically. A different nonce, replaced
resource, stopped/deleted box, unavailable provider or expired/revoked credential
is refused. Validation allows and ownership refusals are audited without tokens.

### Provisioning a managed IDE box

An environment spec with `runtime: "calliope-managed-ide"` and an explicit immutable
`image_tag` selects the managed runtime in the existing box ensure path. The image
must contain the supervised entry point at
`/opt/calliope/scripts/managed-runtime/server.sh` and compatible Calliope extensions.
The paired browser image must contain `/opt/calliope-managed-runtime/browser.cjs`.
Use `idleTimeoutSeconds: 0` explicitly: this mode persists until operator Stop;
it does not silently substitute an idle policy for active or paused conversations.
Boot-time package installation and payload workspace setup are refused; transferred
workspace content is installed by the receiving conversation protocol.

Configure these control-plane settings before selecting that runtime:

| Setting | Required value |
|---|---|
| `MANAGED_IDE_BROWSER_IMAGE` | Immutable `repository@sha256:...` companion image |
| `MANAGED_IDE_STORAGE_CLASS` | Existing CSI StorageClass with `Retain` reclaim policy |
| `MANAGED_IDE_CSI_DRIVER` | Its installed CSI provisioner name |
| `MANAGED_IDE_STORAGE_CAPACITY` | Positive Mi/Gi/Ti capacity, default `20Gi` |
| `PLATFORM_API_URL` | Canonical HTTPS control-plane URL |

Provisioning checks the live StorageClass, CSI inventory and configured RuntimeClass
before applying resources. The first supported image target is AMD64, enforced by
the pod selector. Runtime UID/GID and volume group are 1000. The primary requests
1 CPU/1536 MiB and the browser 500m/768 MiB; these are startup allocations, not a
native-load benchmark. Both containers must pass the actual extension readiness
checks. No server port, Service or Ingress is published.

One dedicated `ReadWriteOncePod` claim holds separate `state` and `workspace`
subpaths, including the persistent browser profile. It has no Job owner reference.
Stop, failed creation and box destruction never delete it. Retained state is recovery
evidence; an existing managed box cannot be automatically repointed or restarted
under another runtime. Explicit recovery and restart recertification are separate work.

The authority record is reserved before creation. Private PVC, Job and Secret objects
are created with Kubernetes POST, stopping on an existing name; none is force-adopted.
Resource UIDs are recorded and rechecked before certification. The primary alone mounts
`/run/astrolift-authority/authority.json`, initially `{ "version": 1, "status": "pending" }`.
After readiness, the control plane publishes `{ version, apiUrl, boxId, ownerEpoch,
podUid, token }` there. The receiver rereads projected credentials and verifies its
Downward API pod/container identity and process-fixed nonce on each authority request.

The reaper renews a certified credential with less than 15 minutes remaining,
preserving its owner epoch and bound process. Secret updates carry observed UID and
resource-version preconditions. Delivery failure rolls back the new credential;
the next sweep retries. Stop serializes cancellation before reservation/apply,
revokes credentials before teardown, and deletes only observed owned Job/Secret UIDs.
The frozen cluster is used even if the organization's default cluster changes.
Partial failures remain visible with retained storage and a retryable Stop path.

Apply `astrolift_agents.0043_managed_runtime_provisioning` before the backend.
For code rollback retain ownership tables, revoke issued credentials and disable
managed consumers; do not drop records containing recovery evidence. Existing
terminal-box recipes retain their established runtime and workspace behavior.

Managed Move capability is still unadvertised. The authenticated transfer receiver,
source integration and live cluster acceptance must land before enabling it.
Actual cluster acceptance must also verify image compatibility, directory ownership
receipts and admission-webhook behavior, including model credentials injected by
workload-identity webhooks. The renderer itself passes approved model configuration
only to the primary container and never copies it into the browser companion.

## Discover and run work

### Saved task backlog

`agentTaskBacklog(orgId: ID!, taskId: ID!)` returns null until a compatible runner
has observed successful native planning/task results. Otherwise it returns
`{ harness, sessionId, revision, updatedAt, items { id text status activeForm details } }`.
Harness values are `claude-code-cli` and `codex-cli`; item status is `pending`,
`in_progress`, or `completed`. `updatedAt` is the server's acceptance timestamp,
not evidence that the pod is still running. An empty `items` list is an explicit
cleared backlog, including after the pod ends. Reads use precisely the task event
permission, token ceiling, owner-operation policy and organization/team visibility.

Runners negotiate `task_backlog_protocol_version: 1` in the authenticated callback
response. The independent `backlog` request field contains `revision`, `harness`,
`session_id`, and `items`; item optional fields use `active_form` and `details`.
It never becomes a task event. The callback's `backlog_revision` acknowledges the
persisted revision. A retry must preserve the identical revision and snapshot;
older/conflicting revisions and changed native session identity are refused.
The runner must preserve state across follow-up turns, and must not restart a
producer against an existing backlog without a corresponding checkpoint.

Snapshots contain at most 256 unique items and 128 KiB of compact UTF-8 JSON.
Session identities are 1–128 ASCII letters/digits/underscore/hyphen; item IDs are
1–128 characters, text and active form at most 8,192 characters, and details at
most 16,384. Text is nonempty; NUL and invalid UTF-8 are refused. Revisions are
positive signed 32-bit integers. Invalid snapshots reject the entire callback,
including accompanying events and terminal result. No state is silently truncated.

Deploy through `astrolift_agents.0041_merge_startup_and_backlog` before
the backend. Roll back code while retaining the nullable column. Older runners
continue to work and report no backlog; newer runners must not publish this
field until support is advertised. Clients against an older schema should
report backlog unavailable rather than infer a plan from prose or task events.

The CLI supports the operator path:

```sh
astro agent workloads ls --json
astro agent ls --status running --json
astro agent dispatch triage --input '{"repository":"example/service"}'
astro agent logs <task-id>
astro agent cancel <task-id>
```

GraphQL exposes `agentFleet`, `agentTasks`, `agentTask`, `agentRuntimes` and
`agentTaskTransitionsSince`, alongside the launch/cancel and steering mutations.

Agent clients connect to the existing MCP endpoint, `/api/mcp/v1/`, with an
Astrolift API token and the normal MCP initialization/session handshake.
`mcp:read` plus `agent.read` reveals discovery tools; dispatch also needs
`mcp:dispatch` plus `agent.dispatch`. Tool discovery reflects both token scopes
and the user's current RBAC grants.

The same `mcp:dispatch` token scope permits box pod discovery and attachment
when the account holds `agent_box.attach` in the target organization. Normal
browser-approved `astro auth login` credentials include this scope; the IDE's
Connect Astrolift command uses that CLI flow. Token scopes remain a ceiling,
not an account grant. Read-only tokens and generic IDE/mobile/browser enrollment
cannot attach. Refresh uses the persisted session kind's current scope set;
no role change or administrator token is needed to repair the CLI scope mapping.

| Tool | Use |
|---|---|
| `astrolift_list_agents` | Discover registered agents and their source/package state. |
| `astrolift_list_runtimes` | Discover the configured runtime names and container images. |
| `astrolift_list_tasks` | List runs by `status`, `agent_slug`, and/or `project_slug`. |
| `astrolift_get_task` | Read one run's lifecycle, placement, result and failure. |
| `astrolift_run_agent` | Launch a registered task agent with `trigger_payload`. |
| `astrolift_cancel_task` | Stop a run through the existing cancellation path. |

For example, call `astrolift_list_tasks` with:

```json
{"status":"running","limit":50}
```

The response contains `tasks` and `next_cursor`. Supply that cursor with the
same filters for the next page; null means the end. Pages sort newest first
with a stable UUID tiebreaker and are capped at 200 rows. Restart at page one
to refresh live state; pagination walks the result set and is not an event
subscription. Task lists omit potentially large results; fetch an individual
task to read its result or failure.

Each task exposes its public ID, status, lifecycle timestamps, pod/namespace
and dispatcher placement when assigned. Dispatcher metadata is a recorded
snapshot with `last_heartbeat_at`, not a synchronous health probe. Credentials
and dispatcher endpoints are not included. Every page is scoped to the active
organization and any team restriction on the token, including shared-agent
read access. A cursor does not grant access to its originating tenant.

Discovery reflects permissions held on an app, project, team or organization;
every object operation checks its target. A selected context grants no extra
authority. Workloads inherit their app; tasks use their recorded project first,
then recorded team, then their live registered-agent app. Invalid recorded
ownership never falls through to the definition. Tasks without usable ownership
require organization authority, retaining access to historical runs for org
operators. These are the same ownership rules used by GraphQL and VNC.

Token permissions and team restrictions remain additional limits over role
bindings. A secondary-team app share needs `viewer` access for reads and
`deployer` or `owner` access for controls, plus the matching permission on that
team. Read-only tokens and shares cannot dispatch or cancel tasks. Package
resources follow the same agent boundary, and an ambiguous authorized agent slug
cannot select a different workload.

An overseer can discover agents, dispatch a task, retain its returned ID, poll
or list its runs, inspect results, and cancel when required. It uses these
ordinary scoped operations; hosting a chat UI does not grant additional
permissions or create a second scheduler. The current MCP launch tool supports
Task-family agents. Service-family agents retain their existing deployment
controls.

## Startup diagnostics

`AstroliftAgentTask.startupDiagnostic` and `AstroliftAgentBox.startupDiagnostic`
are nullable observations of their own scoped pod. The shape is `{ phase,
reason, message, podName, observedAt }`; `observedAt` is an ISO timestamp.
No observation yet means null. Phases are `Pending`, `Starting` (pod Running
but not ready), `Running` (ready), `Succeeded`, `Failed`, or `Unknown`.

A Pending pod with `reason: Unschedulable` exposes the scheduler condition's
bounded message, such as `2 Insufficient cpu`. Container waiting states expose
only their reason, excluding verbose configuration-bearing messages. Pending
is recoverable and does not imply agent inference. K8s tasks remain provisioning
until a pod is ready; an existing external Job prevents redispatch while waiting.

Successful probes refresh `observedAt`; clients should display it as an
observation, not assume continued freshness. Probe errors and a vanished pod
preserve the last snapshot. Ready observations clear reason/message. Task timeout
retains the snapshot and includes its message in `failureMessage` before deleting
the Job. Box restart clears the old snapshot. Existing task/box read permission,
token and tenant scopes govern these fields; no additional cluster grant is needed.

Apply through `astrolift_agents.0041_merge_startup_and_backlog` before deploying
the web/worker image. This merge depends on both additive migrations,
`0040_agent_startup_diagnostic` and `0040_agent_task_backlog`, and supports
installations that already applied either branch. Do not rename or fake either
migration. Roll back compatible images with these columns retained; reversing
an additive migration would discard its saved diagnostics or progress.
Older clients can ignore the additive fields. Older servers do not expose them;
clients may retry an older query only for an unknown-field schema error, never an
authorization or network failure. Capacity changes remain an operator decision.
