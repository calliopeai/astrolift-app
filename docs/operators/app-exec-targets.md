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

Kubernetes exec addresses a pod by name. The UID comparison immediately before
opening is a preflight check: deletion and recreation between that check and the
exec request remains possible. This API does not provide atomic pod-incarnation
binding. Requests for `requireAtomicPodBinding`, `requireActionAdmission`,
`actionAdmissionProof` or session resume are refused. Existing shell authority
permits arbitrary commands inside the selected container; it is not a per-command
permission contract. Audit metadata records the session and reviewed identities
after opening on a best-effort basis, not as durable authorization before open.

Legacy connections omit both `environmentId` and a target. They open only in an
app with exactly one nondeleted environment, using its recorded namespace; an
invalid mapping is refused, with no fallback to the organization's default
cluster. For an app with more than one environment, the `open` gets an `error`
frame and the connection closes with 4409 before any pod is contacted or audit
row is written. The relay never chooses the first or default environment.
A legacy session carries no admitted `target` and no reviewed-mapping recheck
before input. Explicit-environment clients must send a complete review.
AgentBox connections use their separate attach permission and existing target
resolver. Environments created before separate namespaces inherit
the app namespace when their recorded namespace field is blank, as deployed;
the review returns that resolved namespace.

CLI builds that support `--environment <GUID>` use selected-environment review and
admission for both `astro exec` and `astro app exec`. Supplying all of `--workload`,
`--pod` and `--container` requests the exact exec target directly with current
`app.exec_pod` authority; filling in missing selectors uses the selected
environment's inventory and its normal read permissions. Older clients and calls
that omit `--environment` take the legacy path: they still open in an app with
one environment and are refused for an app with several, where only a client
that supports `--environment` can open a shell. The explicit
web shell omits the generic CLI shortcut palette so primary-only examples cannot
be mistaken for its selected target. See `astro docs show environment-actions`
for the consumer contract.

The broader native-client execution contract remains incomplete: action-admission
proofs, mobile-specific audience/token support, durable pre-open audit admission
and atomic pod binding are not implemented here. Native clients must not infer
those capabilities from this WebSocket route. Real PostgreSQL and disposable
Kind tests cover distinct namespaces on one cluster, distinct clusters, recreated
pod rejection, current grant/token/mapping revocation, and disconnect/new-attach
behavior without replacing Kubernetes or authentication transports.

## Session lifecycle

A session belongs to one WebSocket connection. Its `sessionId` is created when
the connection is accepted and returned in `ready` and `replay` frames. A
connection runs at most one command: once an `open` succeeds, another `open`
on it is refused, even after `exit`. Start another command on a new connection.

The session ends when the client sends `close`, when the connection drops, or
when a recheck fails. The command exiting sends `exit` with its code; the
connection stays open until the client closes it. The relay sets no idle
timeout or maximum duration of its own. A proxy or load balancer that closes an
idle connection ends the session like any other disconnect. There is no detach
and reattach: a new connection never joins an earlier shell, even when processes
it started are still running.

Authority is rechecked whenever the client sends `open`, `stdin`, `resize`,
`stdin_eof` or `replay`. A failed credential, actor or organization check closes
the connection with 4401; a failed permission or reviewed-mapping check closes
it with 4403. Either happens before that frame is forwarded. Output already
flowing is not interrupted until the next client frame.

Disconnect closes the Kubernetes exec transport and destroys this connection's
output buffer. It does not guarantee that every process started by the shell
has exited. Frames carry no sequence numbers and the relay does not acknowledge
`stdin`, so input sent shortly before a connection drops may or may not have
reached the process. The last input's outcome can be unknown; inspect state
before repeating a command, and never replay it automatically. The browser
discards input entered before admission or while disconnected and offers an
explicit new shell instead of reconnecting automatically. A pop-out opens a
separate session.

There is no resume. An `open` carrying `resumeSessionId` or `resumable: true` is
refused, and a frame carrying another session's ID is refused without being
forwarded. A new connection receives a new `sessionId` and runs nothing until
it sends its own `open`. `replay` returns up to the last 1000 `stdout`,
`stderr` and `exit` frames of the current connection only. It never contains
input, and a new connection starts with an empty buffer. There is no durable
session resume or input replay.
