# vLLM model hosting: Rust vs Python frontend

Astrolift hosts open-weight models with **vLLM**, the same OpenAI-compatible server on EKS, AKS, GKE and on-prem. A model is a managed service of kind `model_endpoint`, variant `vllm`. Apps and agents bind to it the same way they bind to Bedrock, Azure OpenAI or Vertex, through `MODEL_ENDPOINT_URL`, `MODEL_API_KEY` and `MODEL_DEPLOYMENT_NAME`.

## The two frontends

vLLM runs one engine behind one of two HTTP frontends:

|                                      | Rust                                                         | Python                      |
| ------------------------------------ | ------------------------------------------------------------ | --------------------------- |
| What it is                           | `vllm-rs`, enabled with `VLLM_USE_RUST_FRONTEND=1`           | The original FastAPI server |
| Speed                                | About 5x requests/s on preprocess-heavy loads (per upstream) | Baseline                    |
| Chat and completions (streaming too) | Yes                                                          | Yes                         |
| Tool calling and reasoning           | Some model families (see below)                              | All parsers                 |
| Embeddings, score, rerank            | **Not yet**                                                  | Yes                         |
| Anthropic Messages, Responses API    | Not yet / in progress                                        | Yes                         |

Upstream tracks the gaps in [vllm-project/vllm#44280](https://github.com/vllm-project/vllm/issues/44280). Astrolift keeps the same table in `providers/k8s_native/managed/model_endpoint_vllm.py` (`RUST_TASKS`, `RUST_TOOL_PARSERS`, `RUST_REASONING_PARSERS`), and it is updated as upstream closes items.

## Choosing the frontend

Four levels, where the most specific one that is set wins:

1. **The service:** `frontend = "python"` in the service config.
2. **The model:** `vllm_model_defaults` in the cluster's provider config, keyed by model id or glob:
   ```json
   {
     "vllm_model_defaults": {
       "Qwen/*": { "frontend": "rust" },
       "BAAI/*": { "frontend": "python" }
     }
   }
   ```
3. **The cluster:** `"vllm_frontend": "python"` in the cluster's provider config.
4. **The install:** the admin setting **Model hosting > `VLLM_FRONTEND_DEFAULT`**, which is `rust` by default.

The Deployment records the result as `astrolift.io/vllm-frontend` and the level that decided it as `astrolift.io/vllm-frontend-source` (`service`, `model`, `cluster` or `install`). The service status shows both.

## When Rust can't serve a service

A service that needs something the Rust frontend doesn't serve is **refused, never switched**. That covers `task = "embed"`, `"score"` or `"rerank"`, and any tool or reasoning parser outside the confirmed list. The message names the level that chose Rust, for example:

> the vLLM Rust frontend (chosen by the cluster setting) does not serve task 'embed' yet; set frontend = "python" for this service

Fix it by setting `frontend = "python"` on that service, or by changing the level named in the message.

## Cluster setup

- **`vllm_image` (required):** a pinned image, such as `vllm/vllm-openai@sha256:…`, or the AWS Deep Learning Container image on EKS. There is no default, because a floating tag changes the server under running models.
- **`vllm_storage_class` (optional):** the StorageClass for the weight cache. Empty means the cluster default.
- **GPU nodes:** see [GPU workloads](gpu-workloads.md) for taints, the GPU operator and MIG.
- **`vllm_metrics` (optional):** `{"namespace": "monitoring", "labels": {"release": "kube-prometheus-stack"}}`. With a namespace set, each model gets a ServiceMonitor carrying `labels` (whatever your Prometheus selects on), and its NetworkPolicy admits that namespace on port 8000. The model's metrics panel then shows tokens/sec, running and waiting requests, p95 time to first token and KV cache use. `/metrics` is open; the API key only guards `/v1`.
- **`vllm_agent_test` (optional):** `{"namespace": "astrolift-system", "pod_labels": {"app": "astrolift-agent"}}`. With a namespace set, each model's NetworkPolicy additionally admits that namespace + pod selector on port 8000, so the cluster's keep-alive agent can reach it. Required for the Models page's **Test** action (below); `pod_labels` defaults to the keep-alive Deployment's own labels, so most clusters only need to set `namespace`.

## Testing a model from the Models page

The **Test** button on a `vllm` row (Models page) sends one bounded prompt through the cluster's keep-alive agent -- the control plane never calls the model itself. The agent resolves the model's API key from its own Kubernetes Secret and runs a single chat completion, then reports the reply, latency and token counts back over the same heartbeat channel it already uses.

This needs two things on the cluster, both fail closed with a specific message rather than a bare timeout when missing:

1. A connected agent (`astro operator cluster deploy-agent`; the cluster settings page shows its live status).
2. `vllm_agent_test` set (above), so the agent's NetworkPolicy allowance actually reaches the model.

The prompt and reply never touch Postgres -- they live only in the cache for the duration of the request, capped in size, and the reply is capped to a short response (128 tokens).

## Organization-owned shared deployments

Shared deployments use an explicit organization and available physical cluster,
not a fabricated owning app. The GUID-derived model namespace and resource name
remain stable across display-name changes. Existing app/project services and
legacy `MODEL_*` bindings retain their contracts. Shared creation requires
`CLUSTER_UPDATE` in the exact organization, an unchanged reviewed provider GUID,
and an available enabled managed cluster. In-org catalogue reads require
`ORG_READ`; metadata visibility does not grant deployment authority.

Configure certified runtimes before creation using the Placement step's runtime
setup form or the typed `clusterModelRuntimeSettings` / `updateClusterModelRuntime`
API described below. Existing legacy declarations remain readable by admission;
new setup declarations include serving and resource bounds.
This example is a declaration template: replace both digest placeholders with
verified image digests and certify the actual hardware/node labels first.
Unconfigured modes are refused; neither zero requested GPUs nor an architecture
label proves CPU compatibility.

```json
{
  "vllm_shared_runtimes": {
    "cpu": {
      "version": "0.15.1",
      "package_version": "0.15.1+cpu",
      "image": "registry.example/operator-verified-vllm-cpu@sha256:<64-lowercase-hex-digest>",
      "architecture": "amd64",
      "hardware_certified": true,
      "node_selector": { "example.com/vllm-cpu-certified": "true" }
    },
    "gpu": {
      "version": "0.15.1",
      "package_version": "0.15.1",
      "image": "registry.example/operator-verified-vllm-gpu@sha256:<64-lowercase-hex-digest>",
      "architecture": "amd64",
      "hardware_certified": true,
      "node_selector": { "example.com/vllm-gpu-certified": "true" }
    }
  },
  "vllm_agent_test": {
    "namespace": "astrolift-system",
    "pod_labels": { "app": "astrolift-agent" }
  },
  "vllm_metrics": {
    "namespace": "monitoring",
    "labels": { "release": "kube-prometheus-stack" }
  }
}
```

The shared launcher requires the supported Python vLLM package at startup;
GPU operators may declare a matching released `0.15.1+cuNNN` build. CPU images
require their certified instruction set and runtime dependencies as well as the
selected architecture. CPU requests include explicit KV-cache GiB and memory
strictly larger than that cache. CPU/memory requests are positive and bounded;
GPU mode requires a positive GPU request. Admission checks declarations, not
live capacity, downloaded weights, model fit or successful inference.

Creation currently accepts only a public, ungated Hugging Face repository at a
verified immutable lowercase 40-hex revision. Gated/private/unknown access is
refused. The shared runtime is generation-only, with explicit
`--runner generate --convert none`; embedding/rerank metadata does not authorize
those tasks. The service uses one replica. Existing legacy frontend selection
and non-shared image behavior are unchanged.

### Named subscriptions and reconciliation

A new subscription requires a live same-cluster app environment with exact
`APP_UPDATE` destination permission plus source `ORG_READ`, both subject to
credential ceilings. The owner must enable new subscriptions. Named aliases
match `[a-z][a-z0-9_]{0,31}`; `chat` binds `MODEL_CHAT_ENDPOINT_URL`,
`MODEL_CHAT_API_KEY`, `MODEL_CHAT_DEPLOYMENT_NAME`, `MODEL_CHAT_REGION`,
`MODEL_CHAT_API_STYLE` and `MODEL_CHAT_AUTH_MODE`. Multiple aliases are allowed,
up to 64 active consumers per shared deployment. An alias cannot overwrite an
existing subscription, explicit environment variable or existing Secret/ConfigMap
source prefix. There is no implicit replacement or new legacy `MODEL_*` default.

This initial destination contract supports existing long-running Deployment and
StatefulSet workloads (including agent/workflow workload kinds) in the canonical
app namespace. Empty, Job/CronJob, custom and preview namespace targets are
explicitly refused. Per-subscription Secrets contain only that consumer's key;
the operator key is never copied into app bindings.

Accepted mutations persist pending/revoking state and enqueue revision-bound
Temporal reconciliation; they do not claim readiness or immediate revocation.
Credential changes restart the shared model using **Recreate**, with temporary
unavailability for every subscriber. Independent remaining keys stay unchanged.
The worker confirms the current model generation, authentication revision,
provider GUID and recorded resource, then conditionally updates app pod templates
using observed UID/resourceVersion and verifies actual current Ready pods. It
preserves HPA-owned replica counts. A revoked key is reported revoked only after
the model has restarted with its new snapshot and destination bindings are
removed; updating a Secret alone does not revoke a running frontend's key.

A failed reconciliation exposes a fixed bounded diagnostic and remains failed;
retry/revoke may require operator review. Previously confirmed unrelated bindings
can remain available while a later change fails. Ordinary app deploys materialize
only confirmed coherent subscription references. `ready` is the last confirmed
reconciliation observation, not a continuous health guarantee. The recorded
observation time/generation and actual telemetry remain separate facts.
Deletion requires every subscription's confirmed revocation. Existing cache-PVC
retention semantics still apply; namespace/data cleanup is not implied.

### Private metrics and agent prerequisites

Unlike the legacy single-app `/metrics` behavior described above, shared servers
mount the validated auth middleware and an immutable startup key snapshot.
Subscribers can access the supported model routes but cannot scrape `/metrics`
or operator/config/admin paths. Operator scrape credentials are private. The
shared ServiceMonitor lives in the model namespace, so its operator Secret
selector is local; `vllm_metrics.namespace` admits the monitoring namespace in
NetworkPolicy. Prometheus must discover model namespaces and have permission to
read their ServiceMonitors and referenced scrape Secrets. An installed compatible
ServiceMonitor CRD and policy-enforcing CNI are separate operator prerequisites.

The keepalive agent must be live, include the actual bounded `test_job` relay,
and have network access matching `vllm_agent_test`. Merely seeing an agent version
or accepted NetworkPolicy is not transport/enforcement proof. Runtime tests use
controlled mounted-auth HTTP servers and real pod rollouts; they do not claim a
large model was downloaded or vLLM inference was exercised.

Retire consumers in order: revoke all their subscriptions, wait for each
subscription's confirmed `revoked` state, then retire/deregister the app or its
team/project. Pending, revoking, failed and unconfirmed revocation states block
retirement before side effects. The worker rechecks under app/environment locks
before entering teardown and deleting namespaces/platform records. Once teardown
begins, new subscriptions are refused. Organization retirement additionally
requires deprovisioning all its shared model deployments, including deployments
without subscribers. This release adds no orphan-recovery or instant force-revoke
API; investigate pre-existing corrupted/orphaned records with an operator before
attempting cleanup.

### Hosting wizard setup

`/models/deploy` shows one active step at a time: source, cluster placement and
resources, then the access/license/runtime/resource review. Back and Next preserve
the selected model, cluster, request and unchanged license acknowledgement. Changes
to the reviewed source, target or request still invalidate confirmation. Resource
review actions return to placement and focus the corresponding input. Runtime
settings open in a separate tab so the hosting draft stays in the wizard.

The small-model action resolves `Qwen/Qwen2.5-0.5B-Instruct` through the catalogue
and uses its returned immutable revision. It does not prove access, license
acceptance, CPU/GPU compatibility or model fit. The suggested deployment name is
editable. Cluster choice remains explicit. The small-model preset initializes
1 CPU, 4 GiB memory and 1 GiB CPU KV cache; generic CPU setup starts with
2 CPUs, 8 GiB memory and 2 GiB KV cache. These are editable requests rather than
certified fit or available capacity. CPU KV cache must be an integer from 1 through 1024
GiB, with memory greater than the cache as checked by server admission. GPU
requests omit the CPU cache argument.

Focused tests exercise actual schema-validated HttpLink requests in all eight
locales. Controlled Next/Chromium journeys exercise CPU, GPU and the small-model
preset through review and accepted requests. These fixture receipts prove UI and
request behavior, not successful downloading, scheduling, vLLM inference or live
readiness. Deployment health still requires the actual runtime observations.


## Typed runtime setup and serving bounds

`models.runtime_settings` advertises this additive API, not permission or runtime
readiness. Both reads and writes require a fresh active installation platform
operator, the existing exact organization/cluster gates, and bearer admin,
organization/team and live membership ceilings. The server rechecks authority
and eligible cluster/provider after the final placement lock. Only the selected
`vllm_shared_runtimes.cpu` or `.gpu` entry changes; unrelated provider/auth
configuration and the other compute mode are preserved and never projected.

```graphql
query RuntimeSetup($organizationId: GUID!, $clusterId: GUID!, $expectedProviderId: GUID!) {
  clusterModelRuntimeSettings(organizationId: $organizationId, clusterId: $clusterId,
    expectedProviderId: $expectedProviderId) {
    organizationId clusterId providerId clusterVersion providerVersion
    modes { computeMode configured reason hardwareAdmission
      declaration { image version packageVersion architecture
        nodeSelector { key value } supportedDtypes defaultDtype
        defaultMaxModelLen maxModelLenCeiling defaultMaxNumSeqs maxNumSeqsCeiling
        cpuRequestCeiling memoryRequestCeiling gpuCountCeiling hardwareCertified hardwareEvidence }
    }
  }
}
```

`updateClusterModelRuntime(input: UpdateClusterModelRuntimeInput!)` requires those
exact organization/cluster/provider identities, reviewed `ifMatchVersion` and
`expectedProviderVersion`, a `CPU` or `GPU` compute mode, and a typed declaration.
The declaration requires a digest-pinned image, exact supported vLLM version
`0.15.1`, CPU package `0.15.1+cpu` or supported GPU release package, architecture
and at least one explicit certified hardware selector beyond architecture alone.
It also requires supported/default data types, default/maximum context and
concurrency, positive CPU/memory ceilings and the mode-appropriate GPU ceiling.
Context bounds are 256–131072 tokens and concurrency is 1–4096 sequences.

`hardwareCertified: true` additionally requires `hardwareAttested: true` and a
bounded evidence reference. Record image smoke and node inspection evidence
without credentials/private URLs; the server records the actor and time. An
uncertified declaration may be saved but remains ineligible. This is an operator
assertion, not an automated hardware probe or successful model launch. A tagged
image or a claimed architecture is insufficient for new declarations.

Provisioning and source-preserving settings updates accept optional `dtype`
(`AUTO`, `FLOAT16`, `BFLOAT16`, `FLOAT32`), `maxModelLen` and `maxNumSeqs`. New
runtime declarations supply defaults when creating a model; explicit requests
must match declared dtype support and bounds. Omitting these settings during an
update preserves stored advanced values, including local/HF source identity.
Current declarations are checked again by the actual worker/provider before
credential or Kubernetes writes. Removing certification or narrowing support can
therefore refuse a queued operation. Existing declarations without these new
controls retain their old admission contract; they cannot admit a request that
claims the new serving-controls contract.

The tiny CPU preset requests 1 CPU, 4 GiB memory, 1 GiB CPU KV cache, float32,
256-token context and one concurrent sequence. These limits match the prepared
AVX2 image smoke plan; that plan is not a built image or hardware/runtime proof.
The operator still needs a built digest, image smoke and actual compatible node
inspection. Requested memory above KV cache does not prove weight/KV fit, and
node capacity/selector declarations do not prove successful scheduling. Only
existing generation, auth snapshot, pod rollout and model-server `/health`
observations establish hosted-model readiness. Runtime setup performs no build,
cluster apply, model download or inference.

An accepted setup write survives a failed follow-up read, with a saved-but-read
unconfirmed warning. Refused writes preserve the draft and do not refresh. The
browser binds review/drafts to the actor/organization, selected cluster/provider,
mode and observed versions; server identity/version and authority checks remain
canonical. Retry current reads before another write after an unknown response.
