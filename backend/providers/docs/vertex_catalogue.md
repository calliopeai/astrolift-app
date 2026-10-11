# Vertex metadata catalogue

`gcp.vertex_catalogue.VertexCatalogue` is an internal read-only provider port for
native Endpoint and registered Model metadata (#2269). It is not wired into
GraphQL, a discovery/adoption UI, managed-service lifecycle, bindings or an
inference gateway. Reading a registered Model allocates no serving resources;
creating an Endpoint or deploying a Model is a separate reviewed paid lifecycle.

## Source and admission

The caller must admit the current organization, provider configuration and
credential source before constructing the catalogue. Configuration contains the
exact current GCP project ID or number, configured region, and a `CloudCredential`
with `cloud='gcp'`, ambient mode and the same declared project. Other credential
modes, role pointers, arbitrary endpoints and caller continuation tokens are
unsupported. One private ADC credential is shared by the native clients;
its ambient default project is ignored. Credential objects are not returned.

An optional trusted `checkpoint` callback revalidates the caller's current
authority and source declaration before ADC discovery, after discovery, around
private client construction, and before/after each native RPC. It must complete
with `None` or raise; boolean results do not grant admission. Withdrawal discards
all metadata, prevents later pages/reads and closes partially constructed owned
clients. Any public consumer must supply a real current-admission callback;
omitting it retains the internal caller-admission contract, not an authority
proof. These checks are point-in-time observations, not an atomic native snapshot.

Resource Manager `GetProject` resolves the configured spelling to a valid project
ID and numeric `projects/<number>` name in `ACTIVE` state. Vertex list parents use
that verified number. Every native metadata read is preceded and followed by the
same mapping check, including every independently requested page. Returned
Endpoint/Model names and deployed Model references must belong to the verified
ID/number pair and configured region. Changed, inactive, missing or foreign
mapping refuses the result. Neither display names nor an unverified ID/number
alias substitute for native identity. Cross-region deployed Models are refused
rather than guessed into the configured inventory.

This mapping proves readable project metadata, **not** the actual calling
principal, Google organization/tenant identity, an Astrolift actor's authority,
model invocation access or app access. Ambient credentials can access multiple
projects. No IAM search, permission probe, prediction, deployment, LRO advance
or tag write occurs. Future consumers still need current source and exact action
admission at their own boundary.

## Read contract

- `endpoints(limit=100, max_pages=5)` and `models(...)` are separate inventories.
  Both return native resource names and bounded display names. Endpoint rows
  contain deployed ID, literal Model reference/version, dedicated machine/replica
  metadata, available replica count and traffic percentage. Automatic/shared
  deployment resource bounds remain unknown (`None`), not fabricated zeros.
- `endpoint_detail(name)` and `model_detail(name)` re-read an exact admitted
  resource. Model detail accepts no `@version` or `@alias` caller suffix. The
  unqualified Model observes its current default version. Returned `version_id`
  and `version_aliases`, and deployed Model `@version`/`@alias` spelling, are
  literal observations; mutable/default aliases are never pinned or adopted.
- `invoke_access='unknown'` is explicit. Replica/traffic metadata does not prove
  model readiness, network reachability, health, credentials, protocol support or
  successful inference. An Endpoint can have no deployed Models or traffic.
- `metadata` with no items means a verified empty read. `truncated=True` means
  a page/item bound stopped enumeration; no continuation token escapes. `denied`,
  `not_found`, `error`, and `refused` retain fixed safe reasons and no inventory.
  A later-page failure discards earlier rows rather than presenting partial
  coverage as complete. Identity changes discard all rows.

Limits are 500 output items, five pages, 100 requested items per page, 64 deployed
Models/traffic entries per Endpoint, 32 version aliases per Model, 256 UTF-8 bytes
per projected text field, 4096 bytes per native continuation token, and 2 MiB per
native response. Generated pagination reads one explicit page at a time with no
SDK retries and a ten-second per-RPC timeout. Empty continuing pages consume the
page budget. Oversized, duplicate, malformed or repeated-token responses are
refused; limits do not imply global fleet coverage.

Private production gRPC channels use only
`cloudresourcemanager.googleapis.com` and
`<configured-region>-aiplatform.googleapis.com`, with a 2 MiB receive bound.
There are no redirect-capable HTTP reads or process endpoint overrides. List
read masks request only named projection fields. Detail responses can contain
other fields, but custom labels, descriptions, arbitrary metadata,
artifact/container URIs, service accounts, network endpoints and provider error
bodies are omitted. Only these private transports rebuild GAPIC wrappers on their
authenticated channels without the generated SDK DEBUG payload interceptor;
unrelated Google clients/loggers remain unchanged. Client construction/close and
DEBUG capture are exercised against the generated SDK. Injected native clients
are a trusted caller-owned test port, not an external endpoint/credential API.

## Dependencies and evidence

The GCP extra directly declares `google-cloud-aiplatform>=1.142,<2` and
`google-cloud-resource-manager>=1.19,<2`. Actual generated GAPIC clients and
protobuf serializers are tested at aiplatform 1.142.0 and Resource Manager
1.19.0 with a controlled no-network gRPC channel. Tests cover ID/number mapping,
`ACTIVE`, changed mapping around reads, endpoint-versus-model identity, native
pagination/caps, alias observations, denial/error versus verified-empty, fixed
hosts/private ADC reuse, cleanup and DEBUG payload non-disclosure. Existing
Vertex lifecycle SDK tests remain separate. This is native protocol evidence,
not a live GCP inventory or inference acceptance.

Primary contracts:
[Resource Manager project lookup](https://docs.cloud.google.com/resource-manager/docs/view-update-projects),
[Project identity/state protobuf](https://github.com/googleapis/googleapis/blob/master/google/cloud/resourcemanager/v3/projects.proto),
[Endpoint reads](https://github.com/googleapis/googleapis/blob/master/google/cloud/aiplatform/v1/endpoint_service.proto),
[deployed Model versions/traffic](https://github.com/googleapis/googleapis/blob/master/google/cloud/aiplatform/v1/endpoint.proto),
and [Model reads/default version](https://github.com/googleapis/googleapis/blob/master/google/cloud/aiplatform/v1/model_service.proto).
