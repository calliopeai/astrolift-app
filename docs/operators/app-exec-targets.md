# Open a shell in a reviewed app environment

The app shell selects an environment and shows its cluster and recorded
namespace before opening a connection. Pod and container selection belongs to
that environment. A change to the selected environment, pod, container or review
closes the displayed shell; opening again creates a new connection.

Integrations first query the exact target with current `app.exec_pod` permission:

```graphql
query ReviewExec(
  $app: String!, $workload: String!, $environment: GUID!,
  $pod: String!, $container: String!
) {
  astroliftAppExecTarget(
    appSlug: $app, workloadSlug: $workload, environmentId: $environment,
    podName: $pod, container: $container
  ) {
    workloadId workloadVersion appId appVersion
    environmentId environmentName environmentVersion
    clusterId clusterVersion namespace podName podUid container
    podBinding resumable
  }
}
```

The query checks the current app/environment/cluster ownership, the recorded
namespace and the pod's Astrolift app/environment/workload labels and regular
container. Foreign, deleted, unavailable or unauthorized targets are refused.
Current policy uses the selected environment and cluster region. The result
returns `podBinding: "PREFLIGHT_ONLY"` and `resumable: false`.

Connect to `wss://<host>/app/exec/<app-slug>/<pod-name>?environmentId=<environment-GUID>`
using the existing authenticated browser cookie or an `alft_` bearer token in
the Authorization header. The optional `X-Astrolift-Organization` header selects
the organization for bearer authentication. Never put credentials in the URL.
Send the complete query result, including all GUIDs and versions, in `target`:

```json
{
  "type": "open",
  "container": "main",
  "command": ["sh"],
  "tty": true,
  "target": {
    "workloadId": "<workload GUID>", "workloadVersion": 7,
    "appId": "<app GUID>", "appVersion": 9,
    "environmentId": "<environment GUID>", "environmentName": "staging",
    "environmentVersion": 3,
    "clusterId": "<cluster GUID>", "clusterVersion": 4,
    "namespace": "<reviewed namespace>",
    "podName": "<reviewed pod>", "podUid": "<reviewed pod UID>",
    "container": "main", "podBinding": "PREFLIGHT_ONLY", "resumable": false
  }
}
```

Opening rechecks the locked target, current versions, current credential and
permission, and the current pod UID. It keeps the chosen cluster, driver and
namespace through dispatch. A stale or incomplete review fails without selecting
another environment. Wait for a `ready` frame before sending input. That frame
contains the admitted `target`, a new `sessionId`, `resumable: false`,
`disconnect: "END"` and `inputReplay: false`. Compare the target with the review;
include `sessionId` on `stdin`, `resize`, `stdin_eof` and `replay` frames.
An incorrect session ID is refused. Current actor, organization, credential,
scope ceilings, permission and reviewed mapping are checked again before input.
Revocation ends the connection before forwarding that input.

Disconnect closes the Kubernetes exec transport and destroys this connection's
output buffer. It does not guarantee that every process started by the shell
has exited. The last input's outcome can be unknown when a connection drops;
inspect state before repeating a command. The browser discards input entered
before admission or while disconnected and offers an explicit new shell instead
of reconnecting automatically. A pop-out opens a separate session. `replay`
returns buffered output only on the current connection; a new connection starts
with an empty buffer. There is no durable session resume or input replay.

Kubernetes exec addresses a pod by name. The UID comparison immediately before
opening is a preflight check: deletion and recreation between that check and the
exec request remains possible. This API does not provide atomic pod-incarnation
binding. Requests for `requireAtomicPodBinding`, `requireActionAdmission`,
`actionAdmissionProof` or session resume are refused. Existing shell authority
permits arbitrary commands inside the selected container; it is not a per-command
permission contract. Audit metadata records the session and reviewed identities
after opening on a best-effort basis, not as durable authorization before open.

Legacy connections that omit both `environmentId` and a target retain the first
nondeleted app environment ordered by database ID. They use its recorded
namespace and refuse an invalid first live mapping; they no longer fall back to
the organization's default cluster. Explicit-environment clients must send a
complete review. AgentBox connections use their separate attach permission and
existing target resolver. Environments created before separate namespaces inherit
the app namespace when their recorded namespace field is blank, as deployed;
the review returns that resolved namespace.

The current `astro exec --app=<slug>` command uses that legacy primary-environment
path and has no explicit-environment flag. Its optional `--pod`, `--workload` and
`--container` flags narrow selection within that path; they do not select a
cluster or environment. The explicit-environment web shell omits the legacy CLI
shortcut palette so those examples cannot be mistaken for its selected target.

The broader native-client execution contract remains incomplete: action-admission
proofs, mobile-specific audience/token support, durable pre-open audit admission
and atomic pod binding are not implemented here. Native clients must not infer
those capabilities from this WebSocket route. Real PostgreSQL and disposable
Kind tests cover distinct namespaces on one cluster, distinct clusters, recreated
pod rejection, current grant/token/mapping revocation, and disconnect/new-attach
behavior without replacing Kubernetes or authentication transports.
