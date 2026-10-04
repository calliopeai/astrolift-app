# Shared cluster model serving, v.next

Implementation target for [#2212](https://github.com/calliopeai/astrolift-app/issues/2212).
This document records the agreed behavior; it is not proof that the feature is
released. Deployment and browser verification must accompany the implementation.

## Deploy a model, then subscribe applications

A shared model belongs to an organization and an eligible cluster. Creating it
does not require an application or a placeholder project. Existing app-owned and
project-owned managed services retain their ownership and access rules.

The two journeys are separate:

1. Search Hugging Face, select an exact repository and revision, choose an
   eligible cluster and supported CPU or GPU runtime, then deploy the model.
2. Select an existing model and an eligible app environment, choose a binding
   alias, then subscribe that environment to the model.

An app may subscribe to several models through distinct aliases. Multiple apps
may subscribe to the same deployment. Changing a subscription must not replace
another model's bindings or grant a sibling environment access.

Application targets and model discovery use server-side search and pagination.
A capped initial page is not the complete set of eligible applications. Failed,
pending and unavailable deployments remain inspectable; metadata visibility is
separate from permission and readiness to perform cluster operations.

## Edit a hosted model

`updateClusterModel` requires both organization configuration and cluster update
permission. Its exact organization, cluster, provider and deployment version must
match the reviewed deployment. Optional `name` renames the existing deployment.
Resource edits preserve the admitted local artifact GUID, version and manifest,
or the immutable Hub revision and service-owned private Hub credential reference.
An unavailable or changed source refuses before reconciliation is queued.

Review settings with `clusterModelUpdateAdmission(input: UpdateClusterModelInput)`.
This derives the source from the deployment, rather than reconstructing a public
Hub request from display metadata. Admission checks configuration; an observed
ready generation is still required to establish a running model.

Models use `SHARED` access unless explicitly changed to `DEDICATED`. Shared models
accept independent subscriptions from multiple authorized apps. A dedicated model
remains owned by the organization and cluster, and only the selected app's
environments may subscribe. This does not reserve additional physical hardware.
Select from `clusterModelDedicatedAppsPage`, and submit `dedicatedAppId` with
`ifMatchDedicatedAppVersion` when changing the mode. Other apps' existing bindings
must be fully revoked before the change is accepted; pending revocation is not
sufficient. Retiring or unavailable apps cannot be selected. Subscription writes,
runtime reconciliation and app binding reads enforce the same policy.

## Search and placement evidence

Hugging Face results report upstream metadata and its observation time. A license
label does not establish accepted terms; a gated repository does not establish
download access. A task or architecture label does not prove compatibility with
the selected pinned vLLM runtime. Unknown compatibility remains unknown.

Public catalogue requests use the fixed Hub API with bounded response size,
timeouts and filter-bound pagination. They accept no caller-supplied endpoint or
browser credential. Deployment uses an immutable resolved model revision.

CPU placement requires an explicitly supported pinned CPU runtime. Setting GPU
requests to zero on a GPU image does not establish CPU support. GPU placement
uses explicit resource requests and actual cluster eligibility. Requested memory
or parameter-based size estimates do not establish hardware fit.

The initial pinned shared runtime admits generation models only. Other Hugging
Face tasks remain searchable, but their metadata does not establish a supported
deployment or chat-completion contract. The declared package version must match
the CPU `0.15.1+cpu` build or an explicitly supported CUDA release build; unknown,
development and future versions refuse at runtime startup.

## Subscription access and reconciliation

The shared model has a service-owned namespace derived from persisted tenant,
cluster and service identities. Application traffic needs both the namespace
selector and the exact app/environment pod selectors. On shared physical
clusters, a null cluster organization is not itself permission to deploy.

Each subscription has an independent credential reference. Browser metadata
does not contain its value. Aliases determine which application binding keys are
written; conflicting keys must refuse before side effects. Reads and mutations
recheck current tenant ownership, destination environment, token ceilings and
target identities.

Remove shared model deployments before unregistering or decommissioning their
cluster. App-free pending and failed deployments also block retirement: their
cluster transport remains necessary for cleanup. Retirement and model admission
serialize on the cluster row, and the retirement worker rechecks before changing
the cluster lifecycle. Soft-deleted deployments no longer block retirement.
After a lock wait, retirement re-reads the actor, bearer scopes, grants and actual
cluster region. Bring/refresh and their worker state transitions cannot reopen a
retiring or retired cluster. RBAC removal and cloud teardown hold the placement
lock while checking the current retiring state and absence of live models or
bound environments, then calling the driver. A stale management failure cannot
reset a retiring cluster; an actual retirement failure still enters the retryable
error state. The existing Temporal activity arguments and workflow histories are
unchanged. These guards do not provide exactly-once cloud effects after transport
uncertainty.

New subscriptions require a named alias. Existing legacy `MODEL_*` bindings keep
their behavior. A review captures the exact model, environment and subscription
versions. A changed target invalidates that review, and late mutation responses
cannot update a different context, including an A → B → A selection change.

The agreed initial runtime mechanism uses the supported Python vLLM frontend's
API-key list. Credential changes update a projected Secret and restart the model
with the new list. Unknown key-list runtime support and unsupported frontends
refuse. This restart can interrupt service, so the subscription review must show
the availability impact before submitting.

Attach remains pending until the new model generation is observed ready with
the intended network policy and bindings. Detach removes that environment's
network allowance, removes its key from the running frontend through the rollout,
and only then reports revoked. A failed or incomplete reconciliation stays
visible. Other subscriptions retain their credentials and access.

The runtime hook is `astrolift_shared_model_auth.SharedModelAuth`, mounted from
the dependency-free provider module using the supported Python vLLM middleware
hook. It reads `/var/run/astrolift/model-auth/keys.json` once at startup. The
snapshot has version 1, the expected revision, one private operator key and at
most 64 distinct subscription keys. `ASTROLIFT_MODEL_AUTH_REVISION` must match;
missing, malformed, duplicate or replaced snapshots refuse startup.

Supported inference paths accept a current subscription key or operator key.
Other paths, including `/metrics`, `/load` and runtime configuration routes,
require the operator key. Only exact unauthenticated GET/HEAD `/health` probes
are public. Administrative `/v1` paths do not inherit inference access, and
WebSocket traffic has no supported subscription contract. The ServiceMonitor
must authenticate with the operator Secret. Kubernetes NetworkPolicy cannot
enforce these HTTP path boundaries on its own.

ASGI transport checks prove this hook's routing and startup-snapshot behavior,
including removal of one key while preserving another. They do not establish
that the provider has mounted it, completed a rollout, or served model inference;
those remain separate implementation and release gates.

The opt-in provider check `tests/k8s_native/test_shared_model_kind.py` exercises
the actual native Kubernetes batch adapter against an expendable kind cluster.
It retains the rendered namespace, mounted guard, credential Secret, deployment
revision and network policy, replacing the heavy model process with a controlled
Python HTTP server. Two app keys work initially, subscriber metrics are denied,
and the operator key can scrape. Updating only the Secret leaves the old process
using its startup snapshot. A real Recreate rollout then rejects the removed key,
preserves the remaining key, and removes only the revoked app's network allowance.
The check passed on 2026-09-30. Kind's default CNI does not establish enforcement
of that network policy, and this check does not establish vLLM engine startup,
model downloads, hardware compatibility or inference.

The optional enforcing-CNI case also passed on 2026-09-30, using a separate
disposable kind cluster with Calico 3.32.2. Actual client pods in both subscribed
app namespaces reached the ClusterIP. A different environment in the same app
namespace, a different app in that namespace, and matching app/environment labels
in a foreign namespace were blocked. Subscriber `/metrics` requests reached the
server but failed authorization. After revocation and the actual model rollout,
the revoked namespace was blocked even with the remaining app's valid key; the
remaining app still connected, and its use of the revoked key returned 401.
These checks prove the rendered ingress policy and mounted guard against the
controlled server. They do not establish a production cluster's CNI configuration
or actual model inference.

Run it only with an expendable local kind kubeconfig and the controlled image
`python:3.12-alpine` preloaded on the node:

```sh
ASTROLIFT_MODEL_TEST_KUBECONFIG=/path/to/expendable-kind.kubeconfig \
PYTHONPATH=backend/providers:backend \
python -m pytest backend/providers/tests/k8s_native/test_shared_model_kind.py -q
```

For the enforcing case, create a separate expendable kind cluster with
`disableDefaultCNI: true` and an enforcing Calico installation, following the
[official kind installation guide](https://docs.tigera.io/calico/latest/getting-started/kubernetes/kind).
Select that cluster's kubeconfig explicitly, preload the controlled Python image,
and add `ASTROLIFT_MODEL_TEST_NETWORK_POLICY=1` to the command above. The case
requires ready Calico nodes and verifies allowed and denied connections itself;
setting the variable does not replace the enforcement check.

## Density and operational statistics

Every displayed metric needs a scope, unit, source, observation time and window.
Unavailable, unsupported and stale data remain explicit; missing values are not
zero. A true measured zero remains zero.

| Fact | Evidence |
| --- | --- |
| Configured models, replicas and requests | Authorized persisted configuration |
| Applied configuration | Last successful reconciliation and its identity |
| Running replicas and CPU/memory usage | Observed pods mapped to the exact service |
| Node or device capacity | Timestamped cluster hardware observations |
| Requests, tokens, queues, TTFT and latency | Verified vLLM Prometheus series and window |
| KV-cache use | vLLM cache metric; not device VRAM utilization |
| GPU utilization and VRAM | Verified device/pod attribution; unsupported without it |
| Usage by application | Actual subscription/request metering, never aggregate counters |

Tenant model counts on shared hardware describe that tenant's inventory. They do
not measure whole-cluster saturation. Shared endpoint traffic statistics require
owner-level access; subscribing to an endpoint does not grant other apps' usage.

The initial density scope is `organization_cluster_owned_models`: shared v.next
deployments belonging to the authorized organization in the selected cluster.
Existing app/project endpoints remain available in the model catalogue, but this
scope does not aggregate their potentially hidden ownership descendants. A
bounded inventory reports its limit and truncation; a displayed partial count is
not a fleet total. Whole-cluster hardware capacity is unsupported on install-shared
clusters without a verified mapping to that organization's node pool.

## Release evidence

- [#2213](https://github.com/calliopeai/astrolift-app/issues/2213): real Postgres
  ownership, token ceilings and stale-target tests; provider/Kubernetes checks
  for CPU/GPU placement and independent two-app subscription revocation.
- [#2214](https://github.com/calliopeai/astrolift-app/issues/2214): bounded actual
  Hub transport, revision resolution and observed inventory/metric selectors;
  denied reads perform no upstream requests.
- [#2215](https://github.com/calliopeai/astrolift-app/issues/2215): Storybook first,
  all eight locales, real typed API adapters and browser coverage of both flows.
- Shared model validation reuses the bounded in-cluster
  [real playground transport](model-playground.md), with current shared-owner
  authorization and no caller-provided URL or credential.
- Merge only after schema/contracts and CI pass. Record the exact published image
  pair, migration result and deployed release smoke checks. Local controlled
  model transport is not evidence of live tenant inference.

## Primary runtime references

- [Hugging Face Hub API](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api)
- [vLLM CPU installation](https://docs.vllm.ai/en/latest/getting_started/installation/cpu/)
- [vLLM production metrics](https://docs.vllm.ai/en/latest/usage/metrics/)
