# vLLM model hosting: Rust vs Python frontend

Astrolift hosts open-weight models with **vLLM**, the same OpenAI-compatible server on EKS, AKS, GKE and on-prem. A model is a managed service of kind `model_endpoint`, variant `vllm`. Apps and agents bind to it the same way they bind to Bedrock, Azure OpenAI or Vertex, through `MODEL_ENDPOINT_URL`, `MODEL_API_KEY` and `MODEL_DEPLOYMENT_NAME`.

## The two frontends

vLLM runs one engine behind one of two HTTP frontends:

| | Rust | Python |
|---|---|---|
| What it is | `vllm-rs`, enabled with `VLLM_USE_RUST_FRONTEND=1` | The original FastAPI server |
| Speed | About 5x requests/s on preprocess-heavy loads (per upstream) | Baseline |
| Chat and completions (streaming too) | Yes | Yes |
| Tool calling and reasoning | Some model families (see below) | All parsers |
| Embeddings, score, rerank | **Not yet** | Yes |
| Anthropic Messages, Responses API | Not yet / in progress | Yes |

Upstream tracks the gaps in [vllm-project/vllm#44280](https://github.com/vllm-project/vllm/issues/44280). Astrolift keeps the same table in `providers/k8s_native/managed/model_endpoint_vllm.py` (`RUST_TASKS`, `RUST_TOOL_PARSERS`, `RUST_REASONING_PARSERS`), and it is updated as upstream closes items.

## Choosing the frontend

Four levels, where the most specific one that is set wins:

1. **The service:** `frontend = "python"` in the service config.
2. **The model:** `vllm_model_defaults` in the cluster's provider config, keyed by model id or glob:
   ```json
   {"vllm_model_defaults": {"Qwen/*": {"frontend": "rust"}, "BAAI/*": {"frontend": "python"}}}
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
