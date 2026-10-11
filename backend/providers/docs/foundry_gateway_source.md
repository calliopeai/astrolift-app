# Foundry gateway source observer (#2280)

`azure.foundry_gateway_source` adds provider-private, read-only ARM observation to
the [gateway seam](foundry_chat_gateway.md). It is not wired into a deployment,
public API, registered driver, readiness probe or application connection.

## Exact observation contract

`FoundrySourceTarget` contains the existing immutable gateway declaration plus
resource group, configured region and reviewed model format. Its gateway source
contains organization/service/tenant/subscription GUIDs, account/deployment,
explicit model name/version and the reviewed SHA-256 fingerprint. Versions such
as `default`, `latest`, `auto` and `unknown` are refused by that declaration.

The observer constructs the real `CognitiveServicesManagementClient` lazily after
the required caller checkpoint. It uses API **2025-06-01**, the fixed public ARM
host and `https://management.azure.com/.default`. The Azure extra already declares
SDK >=13.5; controlled native HTTP tests exercise both **13.5.0 and 14.1.0**.
The only native operations are exact account GET, exact deployment GET, then both
again. Lists, key reads, resource writes and inference are unavailable through
this transport. Each GET is checked before send, after the response, throughout
body collection and before returning. Error/redirect responses also perform the
post-response checkpoint. Credential/client discovery and token acquisition are
checked separately. The supplied credential factory is trusted code: it must
bind the intended tenant and credential and preserve token-exchange privacy;
the observer does not discover or attest the actual principal/tenant itself.

Metadata verifies exact case-preserving resource IDs, account/deployment names
and types, AIServices kind, region and the explicit model format/name/version.
Required native etags and creation timestamps participate in the review digest.
The digest binds the target tuple and bounded projected metadata; it deliberately
excludes tags, creators, account API secrets and other unrelated response fields.
It is an observation commitment, **not** an immutable incarnation or an ARM
conditional-write/inference precondition. Repeated reads must agree, and the
digest must match the reviewed source. No mismatch is silently adopted.

The projection preserves custom subdomain, safe endpoint origins/known API base
paths, `disableLocalAuth`, provisioning state, observed `chatCompletion`, upgrade
policy and parent/spillover/model-source/default-project routing. Optional strings
carry both presence and value; missing, explicit null and empty are distinct in
the digest. Regional routing is represented by presence and a bounded digest,
never arbitrary raw regional data. Unknown native properties refuse, preventing
a future routing property from disappearing in generated SDK deserialization.
In particular, SDK13.5 does not model parent/spillover fields: bounded native JSON
is retained privately alongside actual SDK deserialization to inspect them.

## Metadata is separate from eligibility

`FoundrySourceObservation.metadata_verified` means the reviewed metadata matches
the repeated native observations. It does not mean connected, authorized to invoke,
available, ready, or compatible with every gateway request.

| Reason | Meaning |
| --- | --- |
| `authority_unavailable` | Mandatory caller checkpoint refused; no observed success |
| `native_unavailable` / `invalid_response` | Native error, deadline, malformed or unbounded data; no raw diagnostic |
| `source_changed` | Exact source, repeated observation or reviewed fingerprint differs |
| `endpoint_unverified` | Actual custom subdomain and fixed `services.ai.azure.com` origin correspondence is missing |
| `routing_unverified` | Optional routing/upgrade observations are absent or unspecified |
| `mutable_routing` | Parent/spillover/model-source/default-project/region routing or auto-upgrade is present |
| `not_succeeded` | Account or deployment state is not observed `Succeeded` |
| `chat_capability_unverified` | Native `chatCompletion` is absent, unknown or false |
| `runtime_declaration_required` | Always reported: a separate reviewed runtime/model contract and bounded acceptance remain necessary |

The official [deployment GET contract](https://learn.microsoft.com/en-us/rest/api/aiservices/accountmanagement/deployments/get?view=rest-aiservices-accountmanagement-2024-10-01)
defines the model version as optional, with a mutable default when omitted. Its
sample omits routing/upgrade fields; absence does not establish disabled routing.
The [2025-06-01 deployment reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/2025-06-01/accounts/deployments)
describes spillover as another deployment serving throttled requests and lists
`NoAutoUpgrade`. The [account reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/2025-06-01/accounts)
describes `defaultProject` as the target when no project parameter is supplied.
These values are observed and preserved, not inferred from a catalogue entry.

Microsoft's [model management example](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/working-with-models)
returns a `chatCompletion` capability. That is useful observed metadata, not proof
of [Chat-v1](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/chat)
parameter compatibility, `max_completion_tokens`, workload federation or successful
inference. Future orchestration must admit an explicit supported runtime/model
contract and obtain actual bounded acceptance before reporting readiness. It must
not demand an invented ARM compatibility flag or interpret an absent value as
successful runtime evidence.

## Bounds and privacy

There are at most four GETs per observation and one observation at a time per
observer. Native redirects/retries, caller continuation/endpoint input, proxy
environment settings and alternate ARM audiences are refused. Each response is
streamed with identity encoding and a **256KiB** limit before SDK deserialization.
JSON rejects duplicate keys, nesting beyond16 and collections beyond64; endpoint
maps are capped at16. Private query strings, credentials in URLs and unknown API
base paths are not projected. Connection/read timeouts are at most5 seconds and
the observation checks a30-second deadline between waits/chunks. Synchronous
SDK calls cannot interrupt a token factory that ignores its own timeout; the
trusted factory must enforce finite exchange bounds. Cancellation/thread setup
belongs to the future asynchronous integration.

Only typed safe reasons escape failures. SDK/native DEBUG logs are suppressed in
the private I/O context without changing unrelated concurrent logging or levels.
Azure SDK operation/network tracing is explicitly disabled per request; optional
HTTP instrumentation is context-suppressed. Native bodies and credential/transport
ports are excluded from observer repr and the bounded body buffer is cleared after
each observation. Tests capture token/body/header markers, structured logs and
actual SDK/OpenTelemetry spans, including native failures.

The mandatory checkpoint is an integration port, not proof that actual current
actor, credential, membership, provider, source or placement admission was checked
in production. This module neither changes effective account-scope IAM nor proves
per-deployment native IAM isolation. ARM metadata can change after the last GET
and before/during inference: no read-to-invoke CAS or guaranteed executed version
exists here. Model-owned identity, current AKS federation, gateway image/rollout,
current-generation/auth-revision readiness, destination rollout and independently
confirmed revocation remain separate delivery prerequisites. No live Azure call,
paid request, cloud mutation, production readiness or full #2280 completion is
claimed by the SDK/loopback HTTP proof.

Endpoint API-name mappings and the bounded native account capability list also
participate as digests, so a different protocol mapped to the same origin does
not retain the reviewed fingerprint. Arbitrary mapping/capability labels are not
returned. These digests establish unchanged observed metadata, not support for
the gateway request subset.
