# Connecting Zentinelle

Zentinelle governs the agents Astrolift runs. The link is a
`ZentinelleConnection` (`backend/astrolift_operations/models/zentinelle_connection.py`):
a base URL, a sealed install credential, and one `ZentinelleClusterGateway`
per registered cluster. Code: `backend/astrolift_operations/zentinelle_connect.py`.

Two kinds of connection exist:

- **Organization connection.** One per organization. Its clusters are the
  organization's own.
- **Install connection** (`organization = NULL`, #1905). Shared clusters, the
  ones the installer creates with no organization, register through it. Only a
  platform operator can register them.

## From the installer (manage.py)

The installer's agent runs this as a one-off task in the customer account
(calliope-installer#433, #722). The enrollment code is read from an
environment variable, never from an argument, so it is not in the command
line, logs or a process listing.

```bash
# An organization
python manage.py connect_zentinelle --org <slug> --url https://zentinelle.example.net --code-env ZENTINELLE_CODE
# The install connection, then every shared cluster that has no gateway yet
python manage.py connect_zentinelle --install --url https://zentinelle.example.net --code-env ZENTINELLE_CODE --register-shared-clusters
# Gateways reach Zentinelle at an in-cluster address instead of --url
python manage.py connect_zentinelle --org <slug> --url ... --code-env ... --gateway-url http://zentinelle.zentinelle.svc:8000
```

Behaviour:

- Idempotent. A live connection to the same URL is left alone and the code is
  not read; "live" means Zentinelle still accepts the credential, asked each
  run. A connection Zentinelle has revoked is disconnected and connected again
  with the code. A live connection to a different URL is refused: an
  administrator disconnects first.
- `--gateway-url` applies to an existing connection too, and redeploys every
  running gateway so it takes effect.
- `--register-shared-clusters` goes only with `--install`. It registers each
  active, non-retired shared cluster without a gateway, prints one line per
  cluster, and exits non-zero naming any that failed. Running it again picks up
  clusters added since.
- On success the Constance flags `ZENTINELLE_ENABLED` and
  `ZENTINELLE_GATEWAY_ENABLED` are turned on and an audit entry
  (`zentinelle.connect` or `zentinelle.install.connect`) is written.

## From the console, CLI or API

Console: **Secure › Zentinelle** at `/secure/zentinelle` (connect, health,
cluster gateways, usage, policies, audit).

CLI:

```bash
astro zentinelle status
astro zentinelle connect
astro zentinelle disconnect
astro zentinelle gateway-url [<url>]
astro zentinelle cluster register|unregister|enable|disable|rotate <cluster>
```

GraphQL, organization scope: `connectZentinelle`, `setZentinelleGatewayUrl`,
`disconnectZentinelle`, `registerZentinelleCluster`,
`setZentinelleGatewayEnabled`, `rotateZentinelleGatewayCredential`,
`unregisterZentinelleCluster`, `exportZentinelleAudit`. Install scope (platform
operator): `connectZentinelleInstall`, `setZentinelleInstallGatewayUrl`,
`disconnectZentinelleInstall`, `registerZentinelleSharedCluster`,
`moveZentinelleSharedClusterToInstall`, `unregisterZentinelleSharedCluster`.
Reads: `astroliftZentinelleUsage`, `astroliftZentinellePolicies`,
`astroliftZentinelleAuditPage`.

Permissions (`backend/core/permissions.py`): `zentinelle.connect`,
`zentinelle.gateway_manage`, `zentinelle.configure`, `zentinelle.policy_view`,
`zentinelle.policy_edit`, `zentinelle.usage_view`, `zentinelle.audit_view`,
`zentinelle.audit_export`, `zentinelle.status_view`, `zentinelle.conformance_view`.

## The cluster gateway

Registering a cluster deploys the gateway into `astrolift-system`: Deployment
and Service `zentinelle-gateway` on port 8742, Secret
`zentinelle-gateway-credential`, and an HPA (2 replicas minimum,
`ZENTINELLE_GATEWAY_MAX_REPLICAS` maximum, default 6, CPU target 60%; see
[workload-and-node-autoscaling.md](workload-and-node-autoscaling.md)). The
image is the Constance key `ZENTINELLE_GATEWAY_IMAGE`.

- `ZENTINELLE_GATEWAY_ENABLED` is the install-wide switch.
  `manage.py reconcile_zentinelle_gateways` deploys or removes the gateway in
  every registered cluster to match it.
- `setZentinelleGatewayEnabled(enabled: false)` removes one cluster's gateway
  and keeps its registration; `true` applies it again.
- Credential rotation (`rotateZentinelleGatewayCredential`) overlaps the old
  and new credential for a bounded window.
- Each gateway row records `gateway_deployed`, `credential_rotated_at` and
  `last_error`; `astro zentinelle status` prints them per cluster.

## Failure modes

- `connect_zentinelle` says `<ENV> is empty`: the code variable was not set on
  the task; nothing was changed.
- `... is connected to <other url>; an administrator disconnects it before it
  connects elsewhere`: deliberate refusal; run `disconnectZentinelle` (or the
  install variant) first.
- `shared clusters not registered: a, b`: the connection is live, those
  clusters' gateways failed to deploy. Their `last_error` is on the gateway
  row; fix the cluster (reachability, RBAC on `astrolift-system`) and re-run
  with `--register-shared-clusters`, which skips the ones already registered.
- Health reads on **Secure › Zentinelle** (blocks, approvals, budget, gateway
  version, last seen) come from Zentinelle itself; a connection whose
  credential Zentinelle refuses is marked `revoked` by the call that was
  refused (`_mark_revoked`), and `connect_zentinelle` reconnects it with a
  fresh code.
