"""The model the platform itself answers with: Wayfinding and skill assist.

Both called Anthropic's API with ``ANTHROPIC_API_KEY`` and were switched off
on any install that had not been handed one, which was every install. They
now use the install's own cloud by default, through the control plane's own
identity, so they work out of the box on every cloud with no key:

  aws                Amazon Bedrock (Converse), Claude Haiku through the
                     region's cross-region inference profile
  gcp                Vertex AI, Claude Haiku
  azure              Azure OpenAI, the deployment named in the environment
  on-prem / other    any OpenAI-compatible endpoint (a vLLM in the cluster)

Resolution, first match wins:

1. ``ASTROLIFT_PLATFORM_MODEL_PROVIDER=off``: switched off by the operator.
2. ``ASTROLIFT_PLATFORM_MODEL_PROVIDER`` names a provider: that one.
3. ``ANTHROPIC_API_KEY`` is set: Anthropic's API, as before.
4. The control plane's own cloud, from the environment it runs in.

``ASTROLIFT_PLATFORM_MODEL_ID`` overrides the model, and the per-provider
variables below name the endpoint where one is needed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger("astrolift_agents.platform_model")

PROVIDERS = ("anthropic", "bedrock", "vertex", "azure_openai", "openai_compatible")

ANTHROPIC_DEFAULT = "claude-sonnet-4-6"
BEDROCK_FOUNDATION_DEFAULT = "anthropic.claude-haiku-4-5-20251001-v1:0"
VERTEX_DEFAULT = "claude-haiku-4-5@20251001"
VERTEX_DEFAULT_LOCATION = "us-east5"


class PlatformModelError(RuntimeError):
    """The provider refused or could not be reached."""


@dataclass(frozen=True)
class PlatformModel:
    provider: str
    model: str
    region: str = ""
    endpoint: str = ""
    project: str = ""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def bedrock_profile_for(region: str, foundation_model: str = BEDROCK_FOUNDATION_DEFAULT) -> str:
    """The cross-region inference profile for ``region``'s geography.

    Newer Claude models are on-demand only through a profile, and the
    profile's prefix follows where the caller is.
    """
    if region.startswith("eu-"):
        prefix = "eu"
    elif region.startswith("ap-"):
        prefix = "apac"
    else:
        prefix = "us"
    return f"{prefix}.{foundation_model}"


def resolve() -> tuple[PlatformModel | None, str]:
    """``(model, "")``, or ``(None, why there is none)``."""
    chosen = _env("ASTROLIFT_PLATFORM_MODEL_PROVIDER").lower()
    model_id = _env("ASTROLIFT_PLATFORM_MODEL_ID")
    if chosen == "off":
        return None, "turned off for this install"
    if chosen and chosen not in PROVIDERS:
        return None, f"ASTROLIFT_PLATFORM_MODEL_PROVIDER={chosen!r} is not one of {', '.join(PROVIDERS)}"

    if chosen == "anthropic" or (not chosen and _env("ANTHROPIC_API_KEY")):
        if not _env("ANTHROPIC_API_KEY"):
            return None, "the Anthropic provider needs ANTHROPIC_API_KEY"
        return PlatformModel("anthropic", model_id or ANTHROPIC_DEFAULT), ""

    aws_region = _env("ASTROLIFT_PLATFORM_MODEL_REGION") or _env("AWS_REGION") or _env("AWS_DEFAULT_REGION")
    if chosen == "bedrock" or (not chosen and aws_region):
        if not aws_region:
            return None, "the Bedrock provider needs AWS_REGION"
        return PlatformModel("bedrock", model_id or bedrock_profile_for(aws_region), region=aws_region), ""

    project = _env("GOOGLE_CLOUD_PROJECT") or _env("GCP_PROJECT")
    if chosen == "vertex" or (not chosen and project):
        if not project:
            return None, "the Vertex provider needs GOOGLE_CLOUD_PROJECT"
        location = _env("ASTROLIFT_PLATFORM_MODEL_REGION") or VERTEX_DEFAULT_LOCATION
        return PlatformModel("vertex", model_id or VERTEX_DEFAULT, region=location, project=project), ""

    azure_endpoint = _env("AZURE_OPENAI_ENDPOINT")
    if chosen == "azure_openai" or (not chosen and azure_endpoint):
        deployment = model_id or _env("AZURE_OPENAI_DEPLOYMENT")
        if not azure_endpoint or not deployment:
            return None, "the Azure OpenAI provider needs AZURE_OPENAI_ENDPOINT and a deployment"
        return PlatformModel("azure_openai", deployment, endpoint=azure_endpoint.rstrip("/")), ""

    url = _env("ASTROLIFT_PLATFORM_MODEL_URL")
    if chosen == "openai_compatible" or (not chosen and url):
        if not url or not model_id:
            return (
                None,
                "an OpenAI-compatible endpoint needs ASTROLIFT_PLATFORM_MODEL_URL and ASTROLIFT_PLATFORM_MODEL_ID",
            )
        return PlatformModel("openai_compatible", model_id, endpoint=url.rstrip("/")), ""

    return None, "no model is configured and the control plane's cloud could not be told"


@dataclass(frozen=True)
class InstallManagedModel:
    """The open-weight model the install itself serves (calliope-installer#446).

    The installer runs one vLLM on GPU node groups of its own, one node and
    one GPU per replica, and points the platform model at it as
    ``openai_compatible``. Nothing in the app created it and nothing in the
    app may change it, so it is shown read-only and its GPUs count against
    quota.
    """

    model: str
    endpoint: str
    #: ``None`` when the install does not report it
    #: (``ASTROLIFT_PLATFORM_MODEL_REPLICAS``).
    replicas: int | None

    @property
    def gpus(self) -> int:
        """GPUs it holds: one per replica, none counted when unreported."""
        return self.replicas or 0


def install_managed_model() -> InstallManagedModel | None:
    """The install-managed model, or ``None`` when the platform model is not one.

    An ``openai_compatible`` platform model is configured by the install's
    environment, never by a tenant, so it is the install's model. Bedrock,
    Vertex, Azure OpenAI and Anthropic are cloud APIs with no GPUs here.
    """
    model, _ = resolve()
    if model is None or model.provider != "openai_compatible":
        return None
    raw = _env("ASTROLIFT_PLATFORM_MODEL_REPLICAS")
    replicas = int(raw) if raw.isdigit() else None
    return InstallManagedModel(model=model.model, endpoint=model.endpoint, replicas=replicas)


def complete(model: PlatformModel, *, system: str, prompt: str, max_tokens: int = 1024) -> str:
    """One answer from ``model``. Raises :class:`PlatformModelError`."""
    try:
        if model.provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
            message = client.messages.create(
                model=model.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        if model.provider == "bedrock":
            import boto3

            runtime = boto3.client("bedrock-runtime", region_name=model.region)
            response = runtime.converse(
                modelId=model.model,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens},
            )
            return "".join(part.get("text", "") for part in response["output"]["message"]["content"])
        if model.provider == "vertex":
            import anthropic

            client = anthropic.AnthropicVertex(region=model.region, project_id=model.project)
            message = client.messages.create(
                model=model.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        if model.provider in ("azure_openai", "openai_compatible"):
            return _chat_completions(model, system=system, prompt=prompt, max_tokens=max_tokens)
    except PlatformModelError:
        raise
    except Exception as exc:
        raise PlatformModelError(f"{model.provider} call failed: {exc}") from exc
    raise PlatformModelError(f"unknown provider {model.provider!r}")


def _chat_completions(model: PlatformModel, *, system: str, prompt: str, max_tokens: int) -> str:
    import httpx

    body = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if model.provider == "azure_openai":
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
        url = f"{model.endpoint}/openai/deployments/{model.model}/chat/completions?api-version=2024-10-21"
        headers = {"Authorization": f"Bearer {token}"}
    else:
        url = f"{model.endpoint}/chat/completions"
        body["model"] = model.model
        key = _env("ASTROLIFT_PLATFORM_MODEL_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    response = httpx.post(url, json=body, headers=headers, timeout=60)
    if response.status_code >= 400:
        raise PlatformModelError(f"{model.provider} returned {response.status_code}: {response.text[:300]}")
    return response.json()["choices"][0]["message"]["content"]
