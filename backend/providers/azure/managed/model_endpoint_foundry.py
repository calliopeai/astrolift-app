"""Azure AI Foundry model-endpoint driver (#2041).

Foundry serves non-OpenAI models (Meta, Mistral, DeepSeek, Cohere, ...)
from an ``AIServices`` account through the Azure AI model inference
API. The deployment resource and its ARM calls are the same as Azure
OpenAI's, so this driver reuses ``AzureOpenAIDriver`` and changes only
the model spec (a publisher ``model_format`` is required), the default
SKU, the endpoint and the provider aliases. Ownership checks, Key Vault
key handling, the deprovision matrix and every lifecycle method are
inherited unchanged, so the binding and update contracts read the same
code with this class's hooks (telemetry keeps the aoai driver label).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    BindingSchema,
    ValueRef,
)
from azure.managed.model_endpoint_aoai import (
    AzureOpenAIConfig,
    AzureOpenAIDriver,
    AzureOpenAIError,
)


@dataclass(frozen=True)
class AzureFoundryConfig(AzureOpenAIConfig):
    api_version: str = "2024-05-01-preview"
    secret_name_prefix: str = "astrolift-foundry"


class AzureFoundryDriver(AzureOpenAIDriver):
    PROVIDER = "azure_foundry"
    API_STYLE = "azure_ai_inference"

    @driver_op(cloud="azure", driver="model_endpoint_foundry", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["model_format", "model_name"],
            "properties": {
                "model_format": {"type": "string", "description": "Model publisher, e.g. Meta, Mistral AI, DeepSeek"},
                "model_name": {"type": "string"},
                "model_version": {"type": "string"},
                "sku": {"type": "string", "enum": ["GlobalStandard", "Standard", "DataZoneStandard"]},
                "capacity": {"type": "integer", "minimum": 1},
                "rai_policy_name": {"type": "string"},
            },
        }

    @driver_op(cloud="azure", driver="model_endpoint_foundry", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MODEL_ENDPOINT_URL": "Azure AI model inference endpoint of the account",
                "MODEL_API_KEY": "Key Vault ref to the shared account API key",
                "MODEL_DEPLOYMENT_NAME": "Deployment name, sent as the request's model",
                "MODEL_REGION": "Azure location of the account",
                "MODEL_API_STYLE": "Client protocol: 'azure_ai_inference'",
                "MODEL_AUTH_MODE": "Credential kind: 'api_key'",
                "MODEL_ENDPOINT_MODEL_ID": "Alias for MODEL_DEPLOYMENT_NAME",
                "MODEL_ENDPOINT_PROVIDER": "Provider literal: 'azure_foundry'",
                "AZURE_AI_INFERENCE_ENDPOINT": "Alias for MODEL_ENDPOINT_URL",
                "AZURE_AI_INFERENCE_API_VERSION": "Pinned API version for the model inference API",
                "AZURE_AI_INFERENCE_MODEL_NAME": "Underlying model name, informational",
            },
        )

    def _default_sku(self, size: str) -> str:
        return "GlobalStandard"

    def _model_of(self, cfg: dict[str, Any], size: str) -> tuple[str, str, str]:
        if not cfg.get("model_format") or not cfg.get("model_name"):
            raise AzureOpenAIError("azure foundry needs model_format (the publisher) and model_name")
        return cfg["model_format"], cfg["model_name"], cfg.get("model_version") or ""

    def _endpoint_url(self) -> str:
        return f"https://{self._config.account_name}.services.ai.azure.com/models"

    def _alias_envs(
        self, endpoint_url: str, deployment_name: str, model_name: str, api_key: ValueRef
    ) -> dict[str, ValueRef]:
        return {
            "AZURE_AI_INFERENCE_ENDPOINT": ValueRef(literal=endpoint_url),
            "AZURE_AI_INFERENCE_API_VERSION": ValueRef(literal=self._config.api_version),
            "AZURE_AI_INFERENCE_MODEL_NAME": ValueRef(literal=model_name),
        }

    def _binding_notes(self) -> str:
        return (
            "OpenAI-style chat completions at MODEL_ENDPOINT_URL with model=MODEL_DEPLOYMENT_NAME "
            "and the api-key header; the azure-ai-inference SDK reads the same envs."
        )
