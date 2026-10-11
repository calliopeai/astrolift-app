# Existing Azure workload identity reconciliation

This provider-only #2279/#2269 foundation supplies real MSI7 and Authorization
SDK clients for a recorded, existing User-Assigned Managed Identity (UAMI).
It is not connected to app deployment or a public model-connection mutation yet.
The old injected `AzureFederatedIdentityDriver` compatibility path remains
unchanged. Its ambient create/bind/delete calls still refuse without an explicit
legacy client; constructing the new port does not enable name-only legacy writes.

## Caller and identity contract

`AzureOwnedIdentityDriver` accepts a frozen `AzureIdentityContext` and a mandatory
fresh admission/source checkpoint. `FederatedIdentityConfig.owned_reconciler()`
constructs that port after comparing the context's tenant, subscription, resource
group and issuer with configuration. An admitted credential stays private; when
absent, the factory uses the Azure Identity SDK's `DefaultAzureCredential` chain
with the public Entra authority fixed and additional tenants/interactive login
disabled. An explicitly supplied credential is a trusted admitted caller port.
No credential or token is returned in app bindings.

The caller must serialize the app's complete grant/federation union, retain the
recorded identity independently of the cloud response, and check current actor,
organization, app/environment/cluster, credential, desired union and issuer at
every checkpoint. The provider executes this callback before every native request
and after every returned response, including the final observation, subsequent
SDK pages and writes. The callback is a required integration
port, not an implementation of web authorization, DB locking or audit persistence.

Context requires canonical nonzero organization/app/environment/cluster GUIDs,
tenant/subscription GUIDs, exact resource group/UAMI name/full ARM ID, and recorded
client/principal GUIDs. Current authenticated ARM subscription reads must return
the same enabled subscription and tenant. Current UAMI reads must return every
recorded native ID and these existing trusted owner markers:

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
The port never writes tags or fabricates missing native IDs.

The credential wrapper requests only `https://management.azure.com/.default`
in the declared tenant. Subscription/UAMI ARM responses verify native placement
and tenant; they do not prove the credential's principal identity, application
invocation access, or the issuer's current AKS/token behavior. Only public Azure
ARM is supported. Sovereign clouds require a separate admitted contract.

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
remain unconfirmed; they are not silently reported as applied.

The MSI APIs document create-or-update rather than conditional creation and expose
no ETag precondition in this contract. These observations cannot prevent an Azure
operator from replacing/changing a resource between the last read and a write,
including a same-name FIC appearing after an absent GET but before its PUT.
UAMI create/update/delete is therefore deliberately absent, and no remote atomic
incarnation guarantee is claimed. Changed issuers/ownership require explicit
operator recovery with the original recorded identity; this port never retags or
blindly adopts a replacement.

## Configuration is not workload acceptance

Successful reconciliation returns `configuration_observed=true`, KSA client/tenant
annotations, and observed desired FIC/assignment names. `workload_ready=false` and
`propagation_verified=false` remain explicit. This is not model inference proof.

Remaining #2279 work includes durable reviewed caller/union integration and audit
events, supported UAMI creation/lifecycle, live AKS OIDC/workload-identity-profile
verification, current KSA annotations plus pod-template
`azure.workload.identity/use: "true"`, and rollout/token volume/environment and
actual token-exchange acceptance. PostgreSQL/Temporal/workload tests belong to
that integration. Removal here observes owned ARM configuration only; cached
access tokens, external grants and Azure propagation can retain access afterward.

Native MSI7.1.0/Authorization4 HTTP tests cover factory construction, tenant/source
withdrawal, replacement, complete pagination, collisions, partial effects,
foreign preservation, repeated reconciliation, detach and sanitized errors.
They use controlled transports, no Azure calls or credentials.

Primary contracts:
[MSI create-or-update](https://learn.microsoft.com/en-us/rest/api/managedidentity/user-assigned-identities/create-or-update?view=rest-managedidentity-2024-11-30),
[FIC create-or-update](https://learn.microsoft.com/en-us/rest/api/managedidentity/federated-identity-credentials/create-or-update?view=rest-managedidentity-2024-11-30),
[subscription tenant](https://learn.microsoft.com/en-us/rest/api/resources/subscriptions/get?view=rest-resources-2022-12-01),
[federation limits and propagation](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations),
[AKS workload identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview?tabs=python).
