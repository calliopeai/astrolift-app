# Native managed-service binding namespaces

Generated Service endpoints belong to the namespace recorded in the managed
service's four-part handle, independently of the app environment consuming the
binding. Host aliases and URI envelopes use that same locator. A later operator
namespace default cannot redirect a saved service. Reconcile legacy two-part
handles before refreshing these bindings; no namespace is guessed from a name.

New namespace-qualified hosts use `<service>.<namespace>.svc`. The pod's DNS search
suffix supplies its actual cluster domain. CNPG's `DATABASE_URL` selects the
operator's rotating `fqdn-uri` Secret field, which includes the operator's
configured cluster domain; passwords and other credential fields remain Secret
references. Existing externally configured endpoints are retained literally.

References: [Kubernetes cross-namespace Service discovery](https://kubernetes.io/docs/concepts/services-networking/service/),
[CNPG application connections and Secret fields](https://cloudnative-pg.io/docs/devel/applications/).

## Registry audit (#2092)

The current `k8s_native.plugin.PLUGIN.managed_service_drivers` registers **24**
pairs, implemented by 22 modules. Every registered binding implementation was
inspected, including aliases, controller status endpoints and volume bindings.

| Registered kind / variant | Endpoint authority |
|---|---|
| postgres / cnpg | Handle namespace: RW host aliases; operator `fqdn-uri` URL. Preview slices use independent durable credential envelopes and the same parent namespace. |
| redis / operator | Handle namespace: `REDIS_HOST` and `REDIS_URL`. |
| mysql / operator | Handle namespace: `MYSQL_HOST`. |
| document_db / mongodb_operator | Handle namespace: MongoDB SRV URI. |
| event_stream / nats | Handle namespace: NATS broker URI. |
| queue / rabbitmq_operator | Handle namespace: RabbitMQ host. |
| event_stream / kafka_strimzi | Handle namespace: bootstrap host and port; removed legacy namespace guessing. |
| cache / memcached | Already handle-qualified headless host and server list. |
| faas / knative_service | Ready controller status URL; retained literally. |
| api_gateway / gateway_api | Programmed gateway address; retained literally. |
| event_bus / knative_eventing | Ready Broker status address and URI alias; retained literally. |
| workflow_engine / argo_workflows | Explicit operator Argo Server URL; no generated Service endpoint. |
| workflow_engine / temporal | Existing handle-qualified frontend address; operator external address retained. |
| model_endpoint / kserve | Ready inference status HTTP/gRPC URLs; retained literally. |
| model_endpoint / vllm | Existing handle-qualified internal OpenAI endpoint. |
| observability / kube_prometheus_stack | Existing monitoring-namespace-qualified endpoints; explicit endpoints retained. |
| object_store / s3_compatible_existing | Explicit verified external S3 endpoint; retained literally. |
| object_store / seaweedfs_operator | Existing operator-namespace-qualified generated endpoint; explicit endpoint retained. |
| mssql / sqlserver_express | Existing handle-qualified SQL Service host. |
| search / opensearch_operator | Existing handle-qualified search endpoint. |
| vector_index / opensearch_operator_vector | Same endpoint implementation as search. |
| filesystem / nfs_csi | Explicit NFS server and volume metadata; retained literally. |
| filesystem / storage_class_pvc | Consumer-local PVC template; no server endpoint. |
| filesystem / rook_cephfs | Consumer-local CSI PVC template; no server endpoint. |

## Verification boundary

Recording provision/binding tests assert the same persisted cluster and namespace,
including changed operator defaults, both CNPG host aliases and Redis URI aliases.
PostgreSQL tests cover binding synchronization and consumer-namespace Secret
materialization without credential rewriting. The optional disposable kind proof
uses lightweight HTTP servers behind seven generated Service names and a custom
`private.test` cluster DNS domain. This proves DNS and TCP/HTTP routing from another
namespace; it does not certify database operators, authentication or database
protocols. No customer cluster is contacted by these tests.

Focused checks for the ordinary-host leaf: 129 provider checks passed; the two
real PostgreSQL synchronization/materialization checks passed; the opt-in kind
proof passed with all seven generated names reaching the service namespace and
all seven corresponding short names reaching the deliberately wrong consumer
namespace. The disposable `astrolift-binding-dns-2092` cluster was then removed.
Run the proof with `ASTROLIFT_BINDING_DNS_KUBECONFIG` naming that explicitly owned
cluster's private kubeconfig; it skips by default. Provider and backend changed-file
Ruff checks pass. The ordinary-host proof does not certify CNPG operator reconciliation.


## CNPG preview slices

The advertised shared-with-main preview path now configures the selected source
cluster driver, creates an independent login credential, and persists its
`slice_handle` on the consumer attachment. A later deployment reconstructs all
Postgres/DATABASE aliases from that durable identity before resolving credentials.
It never needs the activity's in-memory `env_overrides`, and it never reads the
parent application's credential Secret to fill a preview binding.

Names include both immutable managed-service and consumer-environment GUIDs, so
multiple Postgres services in one namespace do not share a slice Secret or
Database object. Credentials are stored under
`services/<org-guid>/<app-guid>/cnpg-slices/<service-guid>/<environment-guid>`.
The envelope contains username, password, database, namespace-qualified host,
port and a percent-encoded TLS URI; the operator's owned basic-auth Secret contains
only the corresponding username/password. No operator-generated `uri` field is
assumed on that Secret. Retrying a partial role/Database operation reuses the
issued envelope and Secret; it does not replace the password.

The server locks and rechecks current organization/app/team/project, primary and
preview environment, source cluster, service and attachment identities before
provider or secret-store effects. Retired ancestry, another app/cluster, changed
preview policy, malformed/legacy handles and mismatched persisted slice identities
are refused. The final deployment uses the current consumer namespace admitted
under those locks. Native slices cannot be copied to another migration target
cluster. Unrelated binding keys remain available.

The driver verifies parent platform ownership and locator metadata, retained
Secret ownership and the Database's exact parent/name/owner before touching the
credential store. New Secrets and Database objects use atomic create operations,
so an intervening foreign object is refused rather than adopted. Parent role
updates retain the complete Cluster specification and observed UID/resourceVersion;
server activity row locks serialize previews sharing the parent. CNPG still
reconciles asynchronously: accepted manifests and attached bindings do not certify
that a database or login is ready.

New owned preview Databases request `databaseReclaimPolicy: delete`. Cleanup only
removes the exact owned consumer slice, preserves UID/resourceVersion delete
preconditions, and reports an incomplete deletion while its Database finalizer
remains. The credential Secret stays until the Database object is gone. The
portable credential envelope and parent role entry are retained for retry/audit;
this is not a claim of role or portable-credential revocation. See
[CNPG Database lifecycle and reconciliation status](https://cloudnative-pg.io/docs/1.28/declarative_database_management/).

Pre-existing slug-named slices, username-only Secrets, retain-policy Database
objects, and inherited app-private preview attachments without a durable slice
handle require operator reconciliation. The platform does not adopt them, silently move them to a new
parent, or fall back to parent credentials. Explicit unsliced project-shared attachments
keep their existing bindings. Retired owner/source rows also refuse
automatic cleanup; an operator must reconcile their orphaned slice explicitly.

The focused PostgreSQL checks invoke the actual Temporal activity function,
persist the attachment, and make a fresh deployment resolve its exact consumer
Secret. They cover all aliases and URI encoding, one-time credential issuance,
partial create failure/retry, current target/namespace changes, retired/reassigned
ancestry, foreign/legacy slice refusal and two real concurrent database-backed
preview provisions without losing either role. Recording provider clients also
cover atomic-create collisions, ownership refusals before store reads, complete
parent specification preservation and pending-finalizer cleanup. These checks do
not run a real CNPG operator or certify PostgreSQL authentication/role privileges;
the separate disposable kind proof establishes cross-namespace Service DNS and
connection routing only. Release composition and authenticated operator checks
remain separate acceptance evidence for #2092.

Slice-leaf validation: 80 focused provider/SDK checks passed; the full focused
PostgreSQL batch passed 51 checks, followed by 14 current-admission/project-sharing
checks and five final consumer/cleanup recording checks after fixture refinement.
The explicitly owned `test_astrolift_slice_bindings_2092` database was removed.
Changed-file Ruff checks and formatting pass. No production operator or tenant
resource is provisioned by these checks.

## CNPG slice authentication

PostgreSQL's default `PUBLIC CONNECT` privilege and CNPG's default authentication
rule otherwise let a new preview role log into the parent/default databases even
when it cannot read protected application tables. Generated slice roles now have
an explicit TLS/SCRAM allow for their own database followed by a rejection for
other database connections, ahead of existing user rules. Existing PostgreSQL
parameters, identity maps, unrelated authentication rules and other roles survive
the update. Elevated role attributes and inherited role memberships are explicitly
disabled. See [CNPG authentication rule ordering](https://cloudnative-pg.io/docs/current/postgresql_conf/)
and [declarative role management](https://cloudnative-pg.io/docs/1.30/declarative_role_management/).

Reconciliation rereads the exact owned credential Secret and the current complete
parent specification before applying an observed UID/resourceVersion. A confirmed
HTTP 409 conflict permits at most three fresh observations; a replaced parent,
foreign credential/role or another error refuses. An already matching role and
authentication rule set require no write. The retained role, authentication rules
and portable credential envelope after cleanup still do not constitute credential
revocation.

## Disposable operator acceptance (#2092)

The opt-in
`backend/astrolift_workflows/tests/test_preview_slice_cnpg_kind_2092.py` uses only
the explicitly owned `kind-astrolift-cnpg-slice-2092` context from
`ASTROLIFT_CNPG_SLICE_KUBECONFIG`. It configures the actual native driver from the
persisted source cluster, invokes actual activity functions in Temporal's SDK
`ActivityEnvironment`, persists the consumer attachment, and makes a fresh
deployment write the actual Kubernetes consumer Secret. A separate client pod
uses that Secret through `envFrom`; credential values never enter test output or
process arguments. URI authentication reads the complete URI over psql stdin,
without reusing the preceding connection's host/user/password.

The [public proof record](proofs/cnpg-preview-slice-2092.json) identifies actual
disposable owner/source/environment GUIDs, Secret and Database UIDs, all 12 binding
keys, database owner, applied generation and runtime versions. CNPG 1.30.1 with
PostgreSQL 16.15 passed host/URI authentication from another namespace under custom
DNS domain `private.test`, own-database DML, exact ownership, absent elevated
privileges/memberships, denied parent-user credentials, denied parent/default DB
login, and denied plaintext login. The controlled first Database-create failure
left no attachment; retries and repeated calls retained one independently issued
credential envelope, the same physical Secret and database contents.

A held real Database finalizer made cleanup report incomplete and retain its
attachment/credential Secret. After the operator physically dropped the slice
database, removing only the test's hold allowed cleanup to finish; the parent
table remained intact. The login role and portable envelope remained, exactly as
documented. This does not certify credential revocation.

The final operator run passed one check in 30.03 seconds; 48 focused provider
checks and 31 existing real PostgreSQL materialization checks passed. The actual
Kubernetes version was 1.37.0, which CNPG 1.30 lists as tested but unsupported;
supported versions are 1.34–1.36. This is authentication/lifecycle evidence,
not supported-version compatibility certification. See
[CNPG supported releases](https://cloudnative-pg.io/docs/1.30/supported_releases/).
The private persistent credential-store adapter proves issuance/reuse across fresh
adapter reads; it does not certify live Vault integration or a Temporal workflow
server. Production composition and authenticated deployment checks remain separate.

The exact owned kind cluster, private kubeconfig, temporary credential files and
`test_astrolift_cnpg_acceptance_2092` PostgreSQL database were removed after the
proof. The unrelated `edge-poc` cluster was retained.
