# Azure workload identity through owned native ports

Two provider ports in `azure/identity_owned.py` supply real MSI7 and Authorization
SDK clients for the User-Assigned Managed Identity (UAMI) the platform keeps per
app per AKS cluster (#2279, #2269):

- `AzureIdentityProvisioner` observes that UAMI, or creates it where none exists.
- `AzureOwnedIdentityDriver` reconciles the recorded UAMI's federations and role
  assignments, and removes them and the UAMI.

App deployment uses both: `ensure_workload_identity` on an Azure cluster, the
deploy render and identity deprovisioning (`backend/astrolift_services/azure_workload_identity.py`;
operator view in `docs/operators/azure-workload-identity.md`). The old injected
`AzureFederatedIdentityDriver` compatibility path is unchanged and unused in
production. Its create/bind/delete calls still refuse without an explicit legacy
client, and constructing either port does not enable name-only legacy writes.

## Caller and identity contract

`FederatedIdentityConfig.owned_provisioner()` builds the provisioner for an
`AzureIdentityPlacement`. `owned_reconciler()` builds the reconciler for a frozen
`AzureIdentityContext`. Each factory first checks the placement or context
tenant, subscription, resource group and issuer against the configuration, and
the provisioner also checks the location. Both need a fresh admission/source
checkpoint.

The credential stays private. The config carries the cluster's stamped
`CloudCredential`; for Azure that is only ever the ambient identity, which the
ports turn into the Azure Identity SDK's `DefaultAzureCredential` chain, with the
public Entra authority fixed and additional tenants and interactive login
disabled. An explicitly supplied token credential is a trusted admitted caller
port. Any other credential, such as another cloud's or a non-ambient mode, is
refused before a client is built. No credential or token is returned in app
bindings.

The caller must serialize the app's complete grant/federation union, retain the
recorded identity independently of the cloud response, and check current actor,
organization, app/environment/cluster, credential, desired union and issuer at
every checkpoint. The provider executes this callback before every native request
and after every returned response, including the final observation, subsequent
SDK pages and writes. The callback is a required integration port, not an
implementation of web authorization, DB locking or audit persistence.

A placement requires canonical nonzero organization/app/cluster/tenant/subscription
GUIDs, an exact resource group, UAMI name, location and AKS cluster name, and an
HTTPS issuer. A context requires those plus an environment GUID, the full ARM ID
and the recorded client/principal GUIDs. Current authenticated ARM subscription
reads must return the same enabled subscription and tenant. Current UAMI reads
must return every recorded native ID and these existing trusted owner markers:

| Serialized ARM tag | Required value |
| --- | --- |
| `astrolift-managed-by` | `platform` |
| `astrolift-organization-id` | recorded organization GUID |
| `astrolift-app-id` | recorded app GUID |
| `astrolift-cluster-id` | recorded cluster GUID |

Recognized punctuation/case variants must all agree; missing or conflicting
aliases refuse. Environment identity belongs to the caller's desired-union
checkpoint, while the UAMI is shared by that app's environments. Tags and derived
names are integrity checks for a trusted platform owner, not unforgeable authority.
The ports never retag or update an existing UAMI and never fabricate missing
native IDs.

The credential wrapper requests only `https://management.azure.com/.default`
in the declared tenant. Subscription/UAMI ARM responses verify native placement
and tenant; they do not prove the credential's principal identity, application
invocation access, or the issuer's current token behavior. Only public Azure
ARM is supported. Sovereign clouds require a separate admitted contract.

## Provisioning

`ensure(recorded=None)` reads the subscription, then the AKS managed cluster at
the placement's resource group and cluster name. It refuses unless the cluster has
`oidcIssuerProfile.enabled` and `securityProfile.workloadIdentity.enabled`, and
its `oidcIssuerProfile.issuerURL` is exactly the placement issuer. Nothing is
federated to an issuer the cluster does not sign with.

Then it reads the UAMI by name:

- **Absent, nothing recorded.** It creates the UAMI with the four owner tags and
  the placement location, then reads it back.
- **Absent, `recorded` given.** It refuses. A recorded identity that was deleted
  is not re-created, because pods and grants were bound to its principal.
- **Present.** The UAMI must carry this owner's markers. A same-name identity
  without them is refused, never adopted or retagged. With `recorded`, its
  resource, client, principal and tenant IDs must be the recorded ones; a
  replacement is refused.

Refusals raise `AzureIdentityRefused` with a fixed message the platform composed,
naming the identity and resource group, safe to show an operator. Every other
failure, including any SDK error, is the generic "not confirmed" error.

The MSI APIs document create-or-update rather than conditional creation and expose
no ETag precondition in this contract. A same-name UAMI created between the absent
read and the PUT would be retagged and adopted; that window is the residual race
provisioning does not close.

## Reconcile and recovery

`reconcile(federations=..., grants=...)` receives the complete desired union.
Federation subjects are exact `system:serviceaccount:<namespace>:<service-account>`
with the declared HTTPS issuer and single `api://AzureADTokenExchange` audience.
Owned FIC names include app/organization/cluster and exact issuer/subject/audience
digests. Role names include that owner, principal, full ARM scope and a reviewed
built-in role GUID. Role descriptions carry the owner signature. Grants outside
the recorded subscription refuse.

Both complete inventories are validated before effects: at most four pages each,
20 FICs, 128 role assignments, 64 desired grants, and 2 MiB cumulative native
response bodies per operation. Fixed guarded native transports reject external or
other-collection continuations, redirects, unsupported verbs/paths and oversized
responses. Requests have bounded connect/read timeouts and SDK retries disabled.
Failed, truncated, malformed, duplicate or foreign-identity inventories never
authorize writes. Foreign FICs/assignments remain untouched, including equivalent
operator grants; an occupied desired name refuses instead of adopting it.

Writes reread subscription/UAMI ownership, check a named child's current signature
before create/removal, and reobserve the full final union. A partial effect or lost
response returns a generic unconfirmed error. A subsequent admitted reconciliation
first reobserves native state; matching entries need no second PUT. There is no
automatic transport retry or invented rollback. Owned detach preserves remaining
desired/foreign entries and the UAMI itself. Azure principal-replication failures
remain unconfirmed; they are not silently reported as applied. The result names
the owned federations and assignments it removed and the foreign ones it left.

An owned federation whose issuer is not the context's refuses the reconcile, so a
changed issuer is never followed by moving a federation. The caller recovers by
removing the identity under its recorded issuer and provisioning a new one.

## Removal

`remove()` reads the UAMI. If it is already gone, the result says
`identity_absent`; owned assignments that outlived it name a deleted principal,
grant nothing and are not inspected. A complete identity at the recorded name
with another principal is a replacement: the recorded identity is gone too, so
the result says `identity_absent` and `identity_replaced`, and the replacement
is never stripped or deleted. Otherwise the UAMI must still carry the recorded
IDs and owner markers; an incomplete or misnamed response refuses. Removal
strips every owned federation and assignment through the same reconcile, then
deletes the UAMI and reads it again to observe its absence. If a foreign
federation or role assignment remains on the identity, removal stops after
stripping the owned entries: deleting the UAMI would take another party's
federation with it and orphan their assignments. The result names what is left.

A replacement of the UAMI between the last read and the DELETE is a residual race,
as for creation. Removing an assignment or deleting the identity does not revoke
an access token issued before; `propagation_verified` stays false.

## Configuration is not workload acceptance

Successful reconciliation returns `configuration_observed=true`, KSA client/tenant
annotations, and observed desired FIC/assignment names. `workload_ready=false` and
`propagation_verified=false` remain explicit. This is not model inference proof.

Still open under #2279: observing rollout facts on running pods (the projected token
volume, `AZURE_*` environment) and an actual token exchange, and audit events for
the native writes beyond the driver telemetry.

Native MSI7.1.0/Authorization4 HTTP tests cover factory construction, credential
admission, tenant/source withdrawal, AKS issuer and workload identity checks,
creation, foreign and replaced identities, complete pagination, collisions,
partial effects, foreign preservation, repeated reconciliation, detach, removal
and sanitized errors. They use controlled transports, no Azure calls or
credentials.

Primary contracts:
[MSI create-or-update](https://learn.microsoft.com/en-us/rest/api/managedidentity/user-assigned-identities/create-or-update?view=rest-managedidentity-2024-11-30),
[FIC create-or-update](https://learn.microsoft.com/en-us/rest/api/managedidentity/federated-identity-credentials/create-or-update?view=rest-managedidentity-2024-11-30),
[subscription tenant](https://learn.microsoft.com/en-us/rest/api/resources/subscriptions/get?view=rest-resources-2022-12-01),
[federation limits and propagation](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations),
[AKS workload identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview?tabs=python).
