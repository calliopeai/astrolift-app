# Re-pointing a cluster: workload identity roles (#2472)

`manage.py register_tenant_cluster` upserts a tenant cluster row by slug.
Registering another EKS cluster under an existing slug re-points the row:
the installer's region change does this on purpose, registering the target
region's first cluster under the old region's slug after the cut-over
(calliope-installer#540). The row keeps its GUID, its apps and their
environments; only the cluster behind it changes.

Every per-app IAM role Astrolift minted for the row (`astrolift-<org>-<app>`,
the role an app's pods assume through IRSA) trusts the old cluster's OIDC
provider. Until that trust moves, pods on the new cluster cannot assume the
role. An app's next deploy moves it; the re-point moves every app's at once.

## What runs

When the command discovers an AWS cluster (`--auto-discover-aws`, which the
installer sets) whose OIDC issuer differs from the one the row held, it:

1. writes the issuer to `auth_config["cluster_oidc_issuer"]`, the key the
   IRSA driver reads, and to `provider_config` as before;
2. records a `ClusterWorkloadIdentityRebind` naming the new issuer and the
   previous ones;
3. starts `RebindClusterWorkloadIdentityWorkflow-<request guid>`.

The workflow walks every live app with a live environment on the cluster, or
bound to it as its default, in the cluster's organization (any organization
for a shared cluster). For each app's role:

| Trust found | Result |
| --- | --- |
| Federated to a previous issuer of this row | Rewritten to the new issuer: principal and `<issuer>:aud` / `<issuer>:sub` keys. Subjects and the role's policies are kept. |
| Already federated to the new issuer | Nothing to do. |
| Federated to another cluster | Left alone: one role per app, and the cluster that bound it last holds it. |
| No such role | Nothing to do; the app's next deploy creates it. |

A role the platform does not manage (no `astrolift.io/managed-by=platform`
tag), or one whose `astrolift.io/<organization|app|cluster>-id` tags name
another owner, is refused and listed as a failure. Native model connection
roles carry those tags; one owned by this cluster is moved like any other,
after which its own reconcile accepts it again.

Build roles (`astrolift-build-*`) are not touched: every build re-mints its
role against the cluster's current issuer before it runs.

## Where to look

The cluster's overview page shows a **Workload identity roles** card while
the move runs and after it ends: the issuer, how many apps it reached, and
each app whose role did not move, with the cloud's reason. GraphQL:
`astroliftCluster(slug) { workloadIdentityRebind { state issuer appsTotal
appsReconciled failures { app role error } error } }`. It follows the
management error's visibility: callers with `cluster.register` see it; on a
shared cluster, only the platform operator does.

## Retrying

A move that could not start (Temporal unreachable when the control plane
started) or that failed is started again the next time the cluster is
registered: every control-plane start runs the command, and an operator can
run `python manage.py register_tenant_cluster` with the same environment.
Deploying an app also moves its role. A newer re-point supersedes an
unfinished move and carries its issuers forward.

A move refused with "the cluster's OIDC issuer is X, not Y" found the row
pointing at a different cluster from the one it was started for; registering
the cluster again starts a move to the current issuer.
