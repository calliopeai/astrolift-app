# Managed domain routes to public Lambda apps

Refs: #2299.

This path connects a verified, registered public Route53 zone to an existing
Astrolift public Lambda workload and its owned HTTP API. It requires a running
app deployment, active matching Lambda and API managed services, and an exact
current source review. New Deployment/EKS targets and Cloudflare read-only
connections are refused by this path; existing unbound domain behavior remains
separate. No automatic default-domain migration occurs.

Preconditions: `provider_plugin.read` in the organization for the support,
targets, review and operation reads; `provider_plugin.configure` at the mutation
gate, with current `app.update` and `cluster.manage` rechecked inside prepare,
connect and reconcile; a shared zone additionally requires an active platform
superadmin. The selected cluster's signing role, in the cluster's AWS account,
needs the Route53, ACM, API Gateway v2, Lambda and STS actions listed below.
Surface: `astro api graphql -q '<document>'`. Placeholders: `<domain-id>` and
`<domain-version>` are `id` and `version` from
`astro operator domains show <domain-id>` (`astro operator domains list` prints
the ids); `<environment-id>`, `<environment-version>` and `<workload-id>` are
`environmentId`, `environmentVersion` and `workloadId` from
`managedDomainAppTargetsPage`; `<hostname>` is the apex or a strict subdomain;
`<review-sha256>` is `reviewSha256` from `managedDomainAppRouteReview`;
`<request-id>` is a UUID you generate once and retain; `<operation-id>` and
`<operation-version>` are `id` and `version` from `managedDomainAppRouteOperation`.

Use `managedDomainAppRouteSupport` and `managedDomainAppTargetsPage` to select an
admitted app, environment and public workload. Review the apex or a strict
subdomain with `managedDomainAppRouteReview`. The review binds current domain,
app, environment, cluster, deployment and service versions, the manifest and
credential declaration, native account, API integration, Lambda revision and
invoke policy. Read access does not authorize a write. Writes require current
`app.update`, `provider_plugin.configure` and `cluster.manage`; a shared zone
additionally requires an active platform superadmin. Bearer ceilings still apply.

```graphql
query RouteReads($domainId: GUID!, $domainVersion: Int!, $target: ManagedDomainAppRouteTargetInput!) {
  managedDomainAppRouteSupport(domainId: $domainId, expectedDomainVersion: $domainVersion) { allowed reason writer }
  managedDomainAppTargetsPage(domainId: $domainId, expectedDomainVersion: $domainVersion) {
    items { appId appName environmentId environmentVersion workloadId workloadName clusterId }
  }
  managedDomainAppRouteReview(input: $target) {
    reviewSha256 certificateStatus allocationState canPrepare canAttach reason
  }
}
# $target: {"domainId": "<domain-id>", "expectedDomainVersion": <domain-version>,
#           "environmentId": "<environment-id>", "expectedEnvironmentVersion": <environment-version>,
#           "workloadId": "<workload-id>", "hostname": "<hostname>"}
```

The selected cluster signing role needs Route53 zone/record reads and exact
record creation, ACM certificate request/read, API Gateway v2 domain/mapping
reads and creation, certificate association and domain creation tagging, plus
Lambda source/policy and STS identity reads. The separately managed role upgrade
must apply to that actual signing role, not merely the worker task role. Missing
permissions produce unavailable/refused state, never configured success.

For an existing delegated zone without a certificate or associated cluster,
review and explicitly call `prepareManagedDomainAppRoute` with a fresh review
SHA and stable request UUID. Prepare reuses the original zone, retains one ACM
request and its returned certificate identity, creates only absent exact DNS
validation CNAMEs, and publishes the selected cluster and certificate on that
zone. It does not recreate the zone or replace delegation. Wait for the native
certificate to be issued, read current versions, then review Connect again.

For an issued certificate, `connectManagedDomainAppRoute` accepts the reviewed
hostname and the stable request UUID. HTTP API custom-domain allocation happens
inside this original Connect operation. The review explicitly reports pending
allocation with no DNS destination yet. The native returned domain ARN,
regional hostname and hosted-zone ID are retained before fresh admission,
mapping and DNS creation. The generic execute-api API endpoint is never used as
an apex ALIAS destination. The route creates an empty-key mapping to the owned
API's `$default` stage and an exact Route53 A ALIAS, never an apex CNAME or broad
UPSERT. Existing native domains, mappings or conflicting address records refuse
rather than become platform-owned resources.

```graphql
mutation Connect($input: ManagedDomainAppRouteWriteInput!) {
  connectManagedDomainAppRoute(input: $input) {
    ok
    errors { code message }
    data { id version requestId status reason dnsState ingressState tlsState }
  }
}
# $input: the reviewed target fields above plus
#         "reviewSha256": "<review-sha256>", "requestId": "<request-id>"
# prepareManagedDomainAppRoute takes the same input type with its own requestId.
```

Recover with `managedDomainAppRouteOperation` using the original domain GUID and
request UUID. Prepare and Connect have independent original UUIDs. Repeating an
identical accepted request joins its original workflow. A changed tuple is
refused. `reconcileManagedDomainAppRoute` requires a current operation version,
new recovery UUID, current caller write authority and current original admitted
credential/source. It can observe retained original ACKs and advance untouched
stages, but never resends a SENT/UNKNOWN stage. A lost creation ACK stays unknown;
resource names or service tags cannot manufacture the original receipt. A
received original ACK may be retained after withdrawal solely as effect evidence,
without subsequent writes or configured success.

Standalone token revocation/expiry stops further effects. Approved device-session
refresh may follow only its original actor/org/session lineage and intersects the
original accepted scope ceiling with the current token ceiling. Browser session,
sidecar and password-hash freshness are rechecked. These sequential checks do not
claim atomic logout or cancellation of an already sent RPC. Native reads and
writes have finite timeouts and no retry; activity cancellation closes further
checkpoints and drains the outstanding call for a bounded interval. Parent-row
NOWAIT contention reports pending without changing or repeating native intents.

Operation DNS, ingress and TLS fields describe observed configuration. `ready`
means the original native domain/mapping/certificate and exact DNS configuration
were reread; it is not proof of public delegation propagation, browser TLS,
application response, deliverability or live invocation. Diagnostics provide
separate public observations. The operation keeps metadata and credential/source
HMACs, not credentials or application content.

Bound routes are excluded from legacy custom-domain validation, reissue,
assignment, removal, app/service teardown, generic hostname release and zone
provision/delete workers. Those actions refuse with original-route management
required instead of issuing legacy unowned writes. Guarded route removal and
uncertain deletion recovery remain follow-up work. Protected history cannot be
physically deleted or repointed; migration rollback refuses any retained row,
including soft-deleted history. Empty-history rollback restores the prior graph.

## Verify

```bash
astro api graphql -q 'query { managedDomainAppRouteOperation(input: {domainId: "<domain-id>", requestId: "<request-id>"}) { id version status reason dnsState ingressState tlsState canReconcile records { name type target } } }'
```

The operation shows `status` equal to `ready` with the exact A ALIAS record for
`<hostname>`; `pending` or `unknown` with a `reason` means the stage has not been
reread yet, and public delegation, TLS and application response still need the
separate diagnostics.
