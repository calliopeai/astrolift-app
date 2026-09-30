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
