# Original GCP identity source bootstrap (#2278)

This private foundation supplies the original context needed by the Endpoint-only
app plan producer. It is not wired into public app deployment, rendering or
teardown. Saving metadata and creating an app GSA do not prove workload rollout,
federation, impersonation, prediction access or paid inference.

## Server-owned source

`GCPClusterIdentitySource` retains one original registered physical GKE
incarnation for a `TenantCluster`, across all apps and organizations. A null-org
shared cluster remains null-org. The pin is the first explicitly admitted native
observation, not evidence that Astrolift created the cluster or owns the ADC
principal. It records exact provider/scope and identity-declaration hashes, ACTIVE project
ID and numeric name, native `Cluster.id`, zonal or regional GKE location/name,
workload pool, and endpoint/CA digests. Every later caller must observe the same
pin. Role, capacity, metrics and other nonidentity settings belong to the fresh
current operation snapshot, not immutable GSA ownership; compatible changes can
reuse the original observed UID. Changes during a native wait invalidate that
operation. Endpoint/CA route rotation and original Vertex-context region
evolution still require a separate reviewed protocol, not silent repinning. A new app cannot replace it.

`GCPAppIdentitySource` protects original organization/app/cluster/provider and
that cluster pin, accepted signed credential reference, original operation,
workflow/execution, reservation nonce, complete DB union hash and full create
request hash. A source GUID derives a new bounded GSA account ID. Its description
matches the existing GUID-owned `NativeIdentityContext`; display name includes
the original source and create-operation GUIDs. Names and markers never authorize
adoption. Returned numeric `uniqueId` is the immutable account identity.

Normal model saves and PostgreSQL guards reject original-field rewrites, UID
replacement, state regression and record deletion. Retained source rows block
rollback of migration 0040, including OBSERVED rows. Empty source tables can be
rolled down/up. There is no automated retirement, repin, lease takeover or
operator-repair API in this foundation.

## Explicit configuration

The registered GCP cluster must retain its actual endpoint and valid base64 PEM
CA, declare its exact project ID or numeric project reference, native cluster
name and GKE location, and separately declare a regional `vertex_region`:

```json
{
  "project_id": "replace-with-project-id",
  "location": "us-central1-a",
  "cluster_name": "replace-with-gke-name",
  "vertex_region": "us-central1",
  "endpoint_prediction_role": "projects/replace-with-project-id/roles/EndpointPredict"
}
```

`gcp_location` is also accepted as the explicit GKE location key. Zonal locations
are never stripped or guessed into Vertex regions. `region` or the cluster row's
region is insufficient for the Endpoint-only producer. A configured numeric
project/credential declaration is admitted only after exact ACTIVE GetProject
ID↔number correspondence; no principal/tenant claim follows from that read.
The custom role must be current GA and contain exactly
`aiplatform.endpoints.predict`; unsupported/mixed source unions refuse before
GSA creation. Existing setup, admission and credential ceilings remain required.

## Short committed protocol

`bootstrap_original_identity(authority, SourceOperation(...))` requires the real
original `AcceptedAppIdentityAuthority`. Each current checkpoint reloads the
credential, actor, member, target and current policy through the existing fresh
authority helper; no fabricated request or system actor is used. Default approval
facts remain zero. The private bootstrap, original-source reader and producer
accept `deployment_context=DeploymentAuthorityContext(deployment_guid)`. Every
current checkpoint reloads that exact protected Deployment origin, actual human
vote ledger and current policy; a caller-supplied count is not accepted. The
producer evaluates each logical alias against its persisted cluster placement
region, with votes only for the selected environment and zero votes/no deferral
for siblings. The separate Vertex region identifies the native source and does
not determine application placement authority.

Pending source acceptance retains the context as separate immutable metadata
beside the unchanged signed authority fields. Identical caller references from
repeated starts on the same environment cannot substitute a different Deployment
for the pending operation. Legacy pending acceptance without a context refuses
contextual takeover. Evidence-only retention compares the exact original stored
acceptance without a fresh permission lookup. `OBSERVED` source reuse by a later
separately admitted Deployment preserves the original acceptance and UID.

The app/cluster advisory mutex uses an independent autocommit connection and
performs no FK writes. Canonical short NOWAIT transactions lock org, cluster,
provider, team/project, app and source. SDK reads/writes occur outside those
transactions; any enclosing atomic transaction refuses before cloud discovery.
The complete current DB union and pre-GSA project/GKE/Endpoint ownership,
model/version/serving and role observations precede creation.

1. Commit UNSENT with exact original acceptance and create request. Require exact
   native absence; generic existing accounts, conflicting ownership and
   AlreadyExists are not adopted.
2. Recheck current source/authority after reads, commit SENT, then call the native
   create RPC once, without SDK retries. There is no service-account request-ID
   or LRO recovery mechanism.
3. Commit a validated typed returned numeric UID as EVIDENCE. This narrowly
   retained effect evidence can survive later actor/owner/provider withdrawal
   only under the exact original row, reservation, request and accepted-reference
   fences. It supplies no current authority, grants, readiness or follow-on send.
4. Require fresh current authority and exact numeric-ID readback, then repeat
   cluster/Endpoint source observations before OBSERVED and the returned context.

An ambiguous send with no committed returned UID remains UNKNOWN and blocks
resend/new operation/retarget. A lost SENT commit acknowledgement also blocks
automatic send. A committed UID with lost acknowledgement may be re-read under
the original operation and fresh admission; it is never inferred from an email or
mutable marker. `original_identity_context` performs only fresh reads and returns
an already-OBSERVED source, without creating/resuming anything.

Source observations are sequential, not native CAS or a cross-service transaction.
Authority or native metadata may change after a read/send. Evidence retention
neither cancels an old RPC nor authorizes another one. ADC admission records the
current declared credential configuration and accessible exact source; it does
not prove the actual ADC principal, organization or effective future invoke IAM.

## Required caller follow-ups

Public deployment still needs original HTTP admission capture, protected
operation/source references and standalone activity invocation outside the old
atomic legacy helper. The complete producer→GKE preparation→IAM journal→annotation
chain must pass committed original UIDs and current completion fences. Rendering
must use the retained GSA, not app-slug conventions. Teardown and retirement need
original-UID-only, freshly admitted cleanup and unresolved-effect guards, without
same-email fallback. Mixed app unions remain unsupported. Real TokenRequest,
token exchange, GKE rollout and inference acceptance are separate proof gates.

## Native contracts

- [IAM CreateServiceAccount](https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.serviceAccounts/create):
  synchronous ServiceAccount result; no requestId/LRO, account ID 6–30 characters.
- [ServiceAccount identity](https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.serviceAccounts):
  output numeric uniqueId; recreated same-email accounts have another identity;
  the deprecated etag is not a creation precondition.
- [GKE Cluster](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations.clusters):
  output native id/location and workload-pool configuration.

Tests use real PostgreSQL, actual Google GAPIC protobuf serializers and an owned
TLS gRPC server. These fixtures are not production cloud acceptance.

Bootstrap and read-only context recovery use a cumulative 120-second monotonic
budget, including setup, committed reservation, create and repeated readback.
Native calls retain their 10-second per-RPC timeout; expiration stops subsequent
stages and sends but does not cancel an already running SDK call. Full current
provider/auth configuration and complete application source snapshots remain
checked throughout those waits. Pending original reservations also bind the
accepted native Endpoint metadata digest; a changed model/version cannot become
current context merely because account creation returned successfully. Replica
availability is validated against the required current serving minimum, but is
excluded from retained identity so ordinary convergence does not replace or
strand the original source.
