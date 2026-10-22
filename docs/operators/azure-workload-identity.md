# Azure workload identity

On an AKS cluster, an app that binds a managed service runs as a user-assigned
managed identity (UAMI) the platform creates for it, one per app per cluster,
through AKS workload identity (#2279). No cloud key reaches the app.

## What a deploy does

Before it renders, the deploy's workload identity step:

1. Reads the AKS cluster and requires its OIDC issuer and workload identity to be
   enabled, with the issuer it reports equal to the cluster's configured
   `cluster_oidc_issuer`.
2. Observes the UAMI named after the app (the app's ServiceAccount name) in the
   cluster's `resource_group`, or creates it there. A new UAMI is tagged
   `astrolift-managed-by=platform` plus the organization, app and cluster GUIDs.
3. Records the UAMI's resource, client, principal and tenant IDs, and the issuer.
   Every later step holds the identity to that record.
4. Federates each namespace the app runs in on that cluster, for the app's
   ServiceAccount, and reconciles the role assignments its bound services
   declare. Federations and assignments someone else added stay.

The render then gives each pod template `azure.workload.identity/use: "true"` and
runs it as a ServiceAccount annotated with the recorded
`azure.workload.identity/client-id` and `azure.workload.identity/tenant-id`.
Without a recorded identity, the render emits neither.

Each binding's grants show `applied` once Azure lists the assignment, `pending`
when Azure did not confirm the reconcile (the step retries), and `failed` with
the reason for a refusal below. `applied` is the ARM view only: role assignments
can take minutes to take effect, and the platform does not yet observe a running
pod's token exchange.

## Configuration and permissions

The cluster's provider config needs `tenant_id` and `subscription_id` as
lowercase GUIDs, plus `resource_group`, `cluster_oidc_issuer` (exactly as the
cluster reports it, trailing slash included), `location`, and `cluster_name` if
it differs from the cluster's slug.

The control plane's Azure identity needs:

- read on the subscription;
- read on the AKS cluster;
- Managed Identity Contributor on the resource group, to create and delete the
  UAMI and its federated credentials;
- the right to create and delete role assignments, such as Role Based Access
  Control Administrator, at every scope a bound service grants.

Azure allows 20 federated credentials per UAMI, so an app can run in at most 20
namespaces on one cluster. The platform reconciles at most 64 role assignments
per app per cluster.

## Refusals and recovery

Each refusal stops before anything is written:

- **A same-name UAMI without the app's tags.** The platform never adopts or
  retags someone else's identity. Rename or remove that UAMI.
- **The recorded UAMI was deleted, or replaced under its name.** Pods and grants
  were bound to the old principal. Remove the app's workload identity, then
  deploy.
- **The cluster reports a different issuer than its configured
  `cluster_oidc_issuer`, or has OIDC or workload identity off.** Correct the
  configuration or enable both on the cluster, then deploy.
- **The configured issuer changed since the app's identity was federated.** A
  federation is never moved to a new issuer. Remove the app's workload identity,
  then deploy.
- **A bound service grants on a resource outside the cluster's subscription.**
  The grants show `failed` naming the scope. Correct the service, then deploy.

To remove the app's workload identity, use `deleteAppIdentityRole` or deregister
the app. Removal reaches the app's identity on every live Azure cluster, including
one its environments have since migrated off. It strips the platform's federations
and role assignments, deletes the UAMI and soft-deletes the record; the next deploy
creates a new one. A UAMI replaced under the recorded name is left untouched and
the record is soft-deleted; if the replacement lacks the app's tags, the next
deploy refuses it as a same-name UAMI.

A UAMI that carries a federation or role assignment the platform did not add is
left in place after its own are stripped, and the removal fails naming them.
Move or delete them, then remove again. Removal does not revoke a token issued
before it; that ends when the token expires.

Provider contract: `backend/providers/docs/azure_owned_workload_identity.md`.
