# Foundry Chat gateway seam (#2280)

This provider module is a tested ASGI foundation, not a registered driver,
deployment image, available API capability or ready Foundry connection.
`azure.foundry_gateway.authenticated_gateway` wraps a restrictive inner router
with the existing `SharedModelAuth` startup snapshot and subscription counters.
No workflow, UI, source registration or live Azure acceptance is included.

## Contract

| Surface | Supported behavior |
| --- | --- |
| `GET /v1/models` | Local single model: `model-<ManagedService GUID>`; no native catalogue call |
| `POST /v1/chat/completions` | Text-only, non-streaming; exact public model mapped to one native deployment |
| `GET/HEAD /health` | Supplied source/admission checkpoint passed; does not exchange tokens or invoke inference |
| `GET /metrics` | Operator-only existing subscription request/outcome/bytes/duration counters |
| Other paths/methods, queries, encoded path aliases, WebSockets | Refused, including for the operator |

Requests require `model`, 1–128 textual `messages` with `system`, `user` or
`assistant` roles, and integer `max_completion_tokens` from 1 through 2048.
Optional `temperature` (0–2) and `top_p` (0–1) must be finite. `stream` and `store`
may only be false; `n` may only be integer 1. Unknown fields, tools, image/audio
content, `max_tokens`, Responses, embeddings and streaming are unsupported.
This subset does not imply every Foundry model supports these parameters.
Upstream `max_completion_tokens` compatibility must be admitted for the source
before a future connection is advertised as available.

The public-Azure account name derives the sole HTTPS origin:
`https://<account>.services.ai.azure.com/openai/v1/chat/completions?api-version=v1`.
The native credential receives only
`https://cognitiveservices.azure.com/.default`. This follows the
[Chat REST reference](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/chat).
The [Foundry keyless guide](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/configure-entra-id)
uses an AI audience for Responses and resource-scoped inference roles; that is a
separate contract. No audience, endpoint, API-version or protocol fallback occurs.

No caller header is forwarded. The transport generates its own native bearer,
JSON and identity-encoding headers. Upstream headers and error bodies are discarded;
success is normalized to the public stable model ID and supported textual response
fields. Valid native usage may be returned to the caller, but no token/cost metric
is implemented; missing or invalid usage is omitted rather than reported as zero.

## Private integration ports

`FoundryGatewaySource` records org/service/tenant/subscription GUIDs, account and
deployment names, explicit model/version and a source fingerprint. It rejects
obvious mutable version aliases. These declarations are not native observations.
The mandatory async checkpoint receives the immutable source and must reject
withdrawn admission or changed source/operation/placement at every invocation:
initial read, after request/body waits, before token acquisition, after token
acquisition and after the upstream call. The supplied port must also bind current
cluster/provider/revision and actor/credential context; this module has no ORM or
ARM source implementation and does not prove those checks happened in production.

`AzureWorkloadCredential` constructs explicit `WorkloadIdentityCredential` with
fixed Microsoft authority, tenant/client GUIDs and projected token file
`/var/run/secrets/azure/tokens/azure-identity-token`. It does not use a default
credential chain, environment proxy settings or instance-discovery override.
Synchronous token exchange runs in a context-copied worker thread. Cancellation
stops the gateway wait, not a thread already exchanging a token; finite transport
timeouts bound that work and no cancelled waiter proceeds to inference.

`HTTPXNativeTransport` fixes public Azure HTTPS, disables environment configuration,
redirects and retries, and bounds native response bytes. Runtime packaging will
need `httpx` and the Azure extra; no runtime image/dependency installation is
provided by this seam. Optional installed HTTP instrumentation is suppressed
around private I/O. Context-local filters suppress SDK HTTP/header/error logs,
including DEBUG, while unrelated concurrent logs and their levels remain intact.
Injected credential/transport/checkpoint ports are trusted code and must preserve
this privacy contract; newly adopted SDK logging paths need regression coverage.
The gateway does not log prompts/results, headers or native error bodies.

Limits: 1 MiB request and normalized outgoing body, 4 MiB uncompressed native
response, 30-second whole-request timeout, one upstream inference send. Compressed
native responses refuse. An uncertain send/error/timeout is generic and never
retried. Inference may already have consumed paid tokens when a reply is lost;
`native_request_unconfirmed` does not mean no effect. Client cancellation and a
source withdrawal after a send cannot undo that effect. These are per-request
bounds; fleet concurrency/rate limits belong to future deployment configuration.

## Auth and remaining delivery prerequisites

The factory requires a version-2 startup snapshot with revision and subscription
GUID mappings (existing maximum 64). A Secret update alone leaves the old process
snapshot active. A replacement process with the desired revision removes A while
preserving B's unchanged key; old processes must be removed before confirmed
revocation. Requests already admitted before revoke may complete.

Future orchestration must implement model-owned UAMI/federation (current #2279
context is app-owned), controlled account/source authority, AKS OIDC/KSA/pod-template
injection, pinned gateway image, canonical owner resources, network policy,
current-generation/auth-revision readiness, destination secret rollout and confirmed
revocation/unregister. It must preserve current shared/dedicated and approval
contracts and never invoke the legacy paid Foundry/account-key driver. Health here
proves neither AKS federation nor inference; actual identity/native acceptance is
still unverified.

Azure account-scoped IAM is not per-deployment isolation. Owned ARM convergence
preserves foreign resources and cannot certify absence of broader inherited rights.
A trusted gateway confines requests to one source; compromised gateway/native
administrators remain a separate trust boundary. ARM source reads cannot enforce an
immutable native incarnation on inference POST. Upgrade/parent/spillover routing,
source replacement and effective-access limits need explicit admission and tests.
The relevant [native source properties](https://learn.microsoft.com/en-us/python/api/azure-mgmt-cognitiveservices/azure.mgmt.cognitiveservices.models.deploymentproperties?view=azure-python)
are not yet projected/admitted by this module. No live Azure, paid inference,
current-ready model, enforced CNI or full #2280 completion claim follows from the
controlled ASGI/loopback HTTP tests.
