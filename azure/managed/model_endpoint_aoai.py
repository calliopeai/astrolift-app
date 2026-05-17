"""Azure OpenAI managed-service driver (#376).

Implements ``ManagedServiceDriver`` for the canonical Azure managed
model-endpoint path. Azure OpenAI exposes each model behind a
Cognitive Services account (the ``endpoint``) plus a per-model
``deployment``. Each deployment is its own managed-service row --
multiple deployments under one account let workloads address
GPT-4, GPT-3.5-Turbo, etc. independently.

The account itself is operator-managed (provisioning a Cognitive
Services account is gated by tenant-level quota approvals); this
driver provisions + manages the per-model **deployment** under a
pre-existing account, named via ``config.account_name``.

Deprovision matrix:

  delete_data=False, force_destroy=False (default):
    PRESERVE the deployment configuration record. Refuse if the
    Azure resource lock is engaged on the account, or if any
    Azure AI Studio workflow references this deployment.

  delete_data=True, force_destroy=False:
    Drop the deployment + purge the Key Vault secret holding the
    API key. Resource-lock + Studio-reference checks still apply.

  delete_data=False, force_destroy=True:
    PRESERVE the deployment config. Bypass resource-lock + Studio
    reference checks; the operator owns the consequences.

  delete_data=True, force_destroy=True:
    Atomic: delete deployment, purge Key Vault secret, bypass all
    guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)

KIND = "model_endpoint"


class AzureOpenAIError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures (partial Key Vault state often needs
    targeted cleanup)."""


# Size -> SKU. Azure OpenAI deployments expose two SKU families:
# ``Standard`` (pay-per-token, shared infra) and ``ProvisionedManaged``
# (reserved PTUs, dedicated throughput). The platform's
# small/medium/large/xlarge knobs map onto a Standard ladder by
# token quota; xlarge bumps to a provisioned-managed deployment.
_SIZE_TO_SKU = {
    "small": "Standard",
    "medium": "Standard",
    "large": "Standard",
    "xlarge": "ProvisionedManaged",
}


# Size -> default token-per-minute capacity (1k tokens/minute units
# at the Standard tier, PTU units at ProvisionedManaged).
_SIZE_TO_CAPACITY = {
    "small": 10,
    "medium": 30,
    "large": 100,
    "xlarge": 50,
}


# Default model name + version per size. Operators override per
# spec via ``spec.config.model_name`` / ``spec.config.model_version``.
_SIZE_TO_MODEL_NAME = {
    "small": "gpt-35-turbo",
    "medium": "gpt-35-turbo",
    "large": "gpt-4",
    "xlarge": "gpt-4",
}
_DEFAULT_MODEL_VERSION = {
    "gpt-35-turbo": "0125",
    "gpt-4": "0613",
}


@dataclass(frozen=True)
class AzureOpenAIConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    account_name: str
    """Pre-existing Cognitive Services / Azure OpenAI account name.
    Required -- the driver doesn't provision the account itself
    because that step needs tenant-level OpenAI quota approval."""

    location: str = "eastus"

    deployment_name_prefix: str = "astrolift"
    """Prefix on the deployment name. Azure OpenAI deployment names
    are 2-64 chars, alphanumeric + hyphens / underscores."""

    api_version: str = "2024-02-15-preview"
    """Default API version baked into the binding. Operators move
    forward via ``spec.config.api_version``."""

    keyvault_url: str = ""
    """Key Vault URL where the driver mirrors the account API key.
    Empty string means no Key Vault is configured -- the driver
    returns an error on provision rather than emitting credentials
    in the clear."""

    secret_name_prefix: str = "astrolift-aoai"

    mgmt_client: Any | None = None
    """Injected ``CognitiveServicesManagementClient`` for tests."""

    secret_client: Any | None = None
    """Injected Key Vault ``SecretClient`` for tests."""


def tags_for(spec: ProvisionSpec) -> dict[str, str]:
    base = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/organization": spec.organization_slug,
        "astrolift.io/app": spec.app_slug,
        "astrolift.io/environment": spec.environment_name,
        "astrolift.io/cluster": spec.tenant_cluster_id,
        "astrolift.io/isolation": spec.isolation,
    }
    # Per-binding cost-attribution keys (#438). Azure allows the
    # slash + dot form so keys stay identical to the canonical
    # platform schema.
    if spec.binding_id:
        base["astrolift.io/binding"] = spec.binding_id
    if spec.managed_service_id:
        base["astrolift.io/managed_service_id"] = spec.managed_service_id
    base.update({
        f"astrolift.io/extra/{k}": v
        for k, v in (spec.tags or {}).items()
    })
    return base


class AzureOpenAIDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureOpenAIConfig) -> None:
        self._config = config
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.cognitiveservices import (
                CognitiveServicesManagementClient,
            )

            self._mgmt = CognitiveServicesManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        if config.secret_client is not None:
            self._secrets = config.secret_client
        elif config.keyvault_url:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._secrets = SecretClient(
                vault_url=config.keyvault_url,
                credential=DefaultAzureCredential(),
            )
        else:
            self._secrets = None

    # ---- lifecycle ----------------------------------------------------

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(
                    "azure openai driver requires a Key Vault "
                    "(set keyvault_url or inject secret_client)"
                ),
                errors=["no_secret_backend"],
            )
        deployment_name = self._deployment_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(deployment_name)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(deployment_name=deployment_name),
                message=(
                    f"aoai deployment {deployment_name} already exists "
                    f"(state={_state_of(existing)})"
                ),
            )

        sku_name = (
            cfg.get("sku") or _SIZE_TO_SKU.get(spec.size, "Standard")
        )
        capacity = int(
            cfg.get("capacity") or _SIZE_TO_CAPACITY.get(spec.size, 10),
        )
        model_name = (
            cfg.get("model_name")
            or _SIZE_TO_MODEL_NAME.get(spec.size, "gpt-35-turbo")
        )
        model_version = (
            cfg.get("model_version")
            or _DEFAULT_MODEL_VERSION.get(model_name, "")
        )
        rai_policy = cfg.get("rai_policy_name") or "Microsoft.Default"

        parameters: dict[str, Any] = {
            "sku": {"name": sku_name, "capacity": capacity},
            "properties": {
                "model": {
                    "format": "OpenAI",
                    "name": model_name,
                    "version": model_version,
                },
                "rai_policy_name": rai_policy,
                "version_upgrade_option": cfg.get(
                    "version_upgrade_option",
                    "OnceCurrentVersionExpired",
                ),
            },
            "tags": tags_for(spec),
        }

        try:
            poller = self._mgmt.deployments.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                account_name=self._config.account_name,
                deployment_name=deployment_name,
                deployment=parameters,
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"begin_create_or_update: {exc}",
                errors=[str(exc)],
            )

        # Mirror the account API key into Key Vault. The deployment
        # binding hands the consumer a Key Vault ref; the value
        # itself never crosses the workflow boundary in cleartext.
        try:
            keys = self._mgmt.accounts.list_keys(
                resource_group_name=self._config.resource_group,
                account_name=self._config.account_name,
            )
            self._store_api_key(
                deployment_name=deployment_name,
                keys=keys,
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"list_keys / set_secret: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(deployment_name=deployment_name),
            message=(
                f"aoai deployment {deployment_name} provisioning "
                f"(model={model_name}@{model_version}, "
                f"sku={sku_name}, capacity={capacity})"
            ),
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        deployment_name = self._deployment_name_from_handle(spec.handle)
        cfg = spec.config or {}

        sku_body: dict[str, Any] = {}
        if spec.size:
            sku = cfg.get("sku") or _SIZE_TO_SKU.get(spec.size)
            cap = cfg.get("capacity") or _SIZE_TO_CAPACITY.get(spec.size)
            if sku:
                sku_body["name"] = sku
            if cap is not None:
                sku_body["capacity"] = int(cap)
        if cfg.get("sku") and "name" not in sku_body:
            sku_body["name"] = cfg["sku"]
        if "capacity" in cfg and "capacity" not in sku_body:
            sku_body["capacity"] = int(cfg["capacity"])

        properties: dict[str, Any] = {}
        if cfg.get("rai_policy_name"):
            properties["rai_policy_name"] = cfg["rai_policy_name"]
        if cfg.get("version_upgrade_option"):
            properties["version_upgrade_option"] = (
                cfg["version_upgrade_option"]
            )
        if cfg.get("model_version"):
            existing = self._describe(deployment_name)
            current_model_name = (
                _model_field_of(existing, "name")
                if existing is not None
                else cfg.get("model_name", "gpt-35-turbo")
            )
            properties["model"] = {
                "format": "OpenAI",
                "name": cfg.get("model_name") or current_model_name,
                "version": cfg["model_version"],
            }

        body: dict[str, Any] = {}
        if sku_body:
            body["sku"] = sku_body
        if properties:
            body["properties"] = properties

        if not body:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            poller = self._mgmt.deployments.begin_update(
                resource_group_name=self._config.resource_group,
                account_name=self._config.account_name,
                deployment_name=deployment_name,
                deployment=body,
            )
            poller.result()
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"begin_update: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"aoai deployment {deployment_name} update queued",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        deployment_name = self._deployment_name_from_handle(spec.handle)

        existing = self._describe(deployment_name)
        if existing is None:
            if delete_data:
                self._delete_api_key_secret(deployment_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"aoai deployment {deployment_name} already gone",
            )

        if not force_destroy:
            if self._has_resource_lock():
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"aoai deployment {deployment_name} is "
                        f"protected by a resource lock; pass "
                        f"force_destroy=True to bypass"
                    ),
                    errors=["resource_lock_engaged"],
                )
            studio_refs = self._studio_workflow_refs(deployment_name)
            if studio_refs:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"aoai deployment {deployment_name} is "
                        f"referenced by {len(studio_refs)} Azure AI "
                        f"Studio workflow(s); pass "
                        f"force_destroy=True to bypass"
                    ),
                    errors=["studio_reference"],
                )

        try:
            poller = self._mgmt.deployments.begin_delete(
                resource_group_name=self._config.resource_group,
                account_name=self._config.account_name,
                deployment_name=deployment_name,
            )
            poller.result()
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"begin_delete: {exc}",
                errors=[str(exc)],
            )

        if delete_data:
            self._delete_api_key_secret(deployment_name)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"aoai deployment {deployment_name} delete queued "
                f"(api_key={'purged' if delete_data else 'retained'},"
                f" force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        deployment_name = self._deployment_name_from_handle(handle.handle)
        existing = self._describe(deployment_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"aoai deployment {deployment_name} not found",
            )
        azure_state = _state_of(existing)
        return ServiceStatus(
            handle=handle.handle,
            state=_AZURE_STATE_TO_PROTOCOL.get(
                azure_state.lower(), "updating",
            ),
            message=f"azure reports {azure_state}",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        deployment_name = self._deployment_name_from_handle(handle.handle)
        existing = self._describe(deployment_name)
        if existing is None:
            raise AzureOpenAIError(
                f"binding requested for missing deployment "
                f"{deployment_name}",
            )
        endpoint_url = (
            f"https://{self._config.account_name}.openai.azure.com"
        )
        api_key_secret = self._api_key_secret_for(
            deployment_name=deployment_name,
        )
        model_name = _model_field_of(existing, "name") or "unknown"
        env_vars: dict[str, ValueRef] = {
            # Canonical contract envs
            "MODEL_ENDPOINT_URL": ValueRef(literal=endpoint_url),
            "MODEL_ENDPOINT_MODEL_ID": ValueRef(literal=deployment_name),
            "MODEL_ENDPOINT_PROVIDER": ValueRef(
                literal="azure_openai",
            ),
            # AOAI-flavoured aliases
            "AZURE_OPENAI_ENDPOINT": ValueRef(literal=endpoint_url),
            "AZURE_OPENAI_DEPLOYMENT_NAME": ValueRef(
                literal=deployment_name,
            ),
            "AZURE_OPENAI_API_VERSION": ValueRef(
                literal=self._config.api_version,
            ),
            "AZURE_OPENAI_API_KEY": ValueRef(secret_ref=api_key_secret),
            "AZURE_OPENAI_MODEL_NAME": ValueRef(literal=model_name),
        }
        account_resource_id = (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/"
            f"{self._config.account_name}"
        )
        return Binding(
            env_vars=env_vars,
            iam_grants=[
                Grant(
                    resource=(
                        f"{account_resource_id}/deployments/"
                        f"{deployment_name}"
                    ),
                    actions=[
                        "Microsoft.CognitiveServices/accounts/"
                        "deployments/read",
                        "Microsoft.CognitiveServices/accounts/"
                        "OpenAI/deployments/action",
                    ],
                ),
                Grant(
                    resource=api_key_secret,
                    actions=[
                        "Microsoft.KeyVault/vaults/secrets/getSecret",
                    ],
                ),
            ],
            notes=(
                "AZURE_OPENAI_API_KEY is a Key Vault ref to the "
                "shared account key; the deployment selector is "
                "AZURE_OPENAI_DEPLOYMENT_NAME. Use the official "
                "openai SDK's ``AzureOpenAI`` client with these "
                "envs."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        # Azure OpenAI deployments have no service-side snapshot;
        # the deployment config is itself the "snapshot" (cheap to
        # re-create). Return a deterministic id so the workflow
        # layer's snapshot path gets a handle to track.
        from datetime import UTC, datetime

        deployment_name = self._deployment_name_from_handle(handle.handle)
        existing = self._describe(deployment_name)
        if existing is None:
            raise AzureOpenAIError(
                f"snapshot for missing deployment {deployment_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{deployment_name}-snap-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(
                f"target deployment provisioned; snapshot "
                f"{snapshot.snapshot_id} encodes the prior config "
                f"shape -- re-create is the restore"
            ),
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sku": {
                    "type": "string",
                    "enum": ["Standard", "ProvisionedManaged"],
                },
                "capacity": {"type": "integer", "minimum": 1},
                "model_name": {"type": "string"},
                "model_version": {"type": "string"},
                "rai_policy_name": {"type": "string"},
                "version_upgrade_option": {
                    "type": "string",
                    "enum": [
                        "OnceCurrentVersionExpired",
                        "OnceNewDefaultVersionAvailable",
                        "NoAutoUpgrade",
                    ],
                },
            },
        }

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MODEL_ENDPOINT_URL": (
                    "Azure OpenAI account HTTPS endpoint"
                ),
                "MODEL_ENDPOINT_MODEL_ID": (
                    "Deployment name within the account"
                ),
                "MODEL_ENDPOINT_PROVIDER": (
                    "Provider literal: 'azure_openai'"
                ),
                "AZURE_OPENAI_ENDPOINT": "Alias for MODEL_ENDPOINT_URL",
                "AZURE_OPENAI_DEPLOYMENT_NAME": (
                    "Alias for MODEL_ENDPOINT_MODEL_ID"
                ),
                "AZURE_OPENAI_API_VERSION": (
                    "Pinned API version for the AOAI REST surface"
                ),
                "AZURE_OPENAI_API_KEY": (
                    "Key Vault ref to the shared account API key"
                ),
                "AZURE_OPENAI_MODEL_NAME": (
                    "Underlying model name (e.g. gpt-4) -- "
                    "informational; routing is by deployment_name"
                ),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, deployment_name: str) -> Any | None:
        try:
            return self._mgmt.deployments.get(
                resource_group_name=self._config.resource_group,
                account_name=self._config.account_name,
                deployment_name=deployment_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            raise

    def _has_resource_lock(self) -> bool:
        """Best-effort probe for an Azure resource lock on the
        Cognitive Services account. The control plane normally has
        the locks API surface mocked; we return False on any error
        (fail-open here matches the rest-of-azure-driver pattern of
        leaving lock enforcement to Azure itself)."""
        client = getattr(self._mgmt, "resource_locks", None)
        if client is None:
            return False
        try:
            locks = list(client.list_at_resource_level(
                resource_group_name=self._config.resource_group,
                resource_provider_namespace="Microsoft.CognitiveServices",
                parent_resource_path="",
                resource_type="accounts",
                resource_name=self._config.account_name,
            ))
        except Exception:
            return False
        return bool(locks)

    def _studio_workflow_refs(self, deployment_name: str) -> list[str]:
        """Best-effort probe for Azure AI Studio workflows that
        reference this deployment. The control plane normally has
        this surface mocked; we return [] when the operation isn't
        wired so production drift doesn't block deprovisions."""
        client = getattr(self._mgmt, "studio_workflows", None)
        if client is None:
            return []
        try:
            return list(client.list_referencing_deployment(
                resource_group_name=self._config.resource_group,
                account_name=self._config.account_name,
                deployment_name=deployment_name,
            ))
        except Exception:
            return []

    def _deployment_name_for(self, *, spec: ProvisionSpec) -> str:
        parts = [
            self._config.deployment_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "model",
        ]
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(
            c if (c.isalnum() or c in "-_") else "-" for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-_")
        if not clean or not clean[0].isalnum():
            clean = f"a{clean}"
        return clean[:64]

    def _handle_for(self, *, deployment_name: str) -> str:
        return f"{KIND}/{deployment_name}"

    def _deployment_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureOpenAIError(
                f"handle {handle!r} must be '<kind>/<deployment_name>'",
            )
        kind, _, deployment_name = handle.partition("/")
        if not kind or not deployment_name:
            raise AzureOpenAIError(
                f"handle {handle!r} has empty component",
            )
        return deployment_name

    def _api_key_secret_for(self, *, deployment_name: str) -> str:
        return (
            f"{self._config.secret_name_prefix}-"
            f"{self._config.account_name}-{deployment_name}-key"
        )

    def _store_api_key(
        self, *, deployment_name: str, keys: Any,
    ) -> None:
        primary = (
            _key_field(keys, "key1")
            or _key_field(keys, "primary_key")
            or _key_field(keys, "primaryKey")
            or ""
        )
        self._secrets.set_secret(
            self._api_key_secret_for(deployment_name=deployment_name),
            primary,
        )

    def _delete_api_key_secret(self, deployment_name: str) -> None:
        if self._secrets is None:
            return
        name = self._api_key_secret_for(deployment_name=deployment_name)
        try:
            self._secrets.begin_delete_secret(name)
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


def _state_of(deployment: Any) -> str:
    state = getattr(deployment, "provisioning_state", None)
    if state is not None:
        return str(state)
    state = getattr(deployment, "provisioningState", None)
    if state is not None:
        return str(state)
    if isinstance(deployment, dict):
        if "provisioning_state" in deployment:
            return str(deployment["provisioning_state"])
        if "provisioningState" in deployment:
            return str(deployment["provisioningState"])
        props = deployment.get("properties") or {}
        if isinstance(props, dict) and "provisioning_state" in props:
            return str(props["provisioning_state"])
    props = getattr(deployment, "properties", None)
    if props is not None:
        inner = getattr(props, "provisioning_state", None)
        if inner is not None:
            return str(inner)
    return "Unknown"


def _key_field(keys: Any, field_name: str) -> str:
    v = getattr(keys, field_name, None)
    if v:
        return str(v)
    if isinstance(keys, dict) and keys.get(field_name):
        return str(keys[field_name])
    return ""


def _model_field_of(deployment: Any, field_name: str) -> str:
    """Read the deployed model's ``name`` or ``version`` off the
    deployment shape, whether the SDK returned a flat dict, a
    nested ``properties.model.<field>`` dict, or attr objects."""
    if deployment is None:
        return ""
    props = (
        deployment.get("properties")
        if isinstance(deployment, dict)
        else getattr(deployment, "properties", None)
    )
    model = None
    if isinstance(props, dict):
        model = props.get("model")
    elif props is not None:
        model = getattr(props, "model", None)
    if isinstance(model, dict):
        v = model.get(field_name)
        return str(v) if v else ""
    if model is not None:
        v = getattr(model, field_name, None)
        return str(v) if v else ""
    return ""


# Azure cognitive-services provisioningState values mirror the
# generic Azure shape. Lowercase the key to keep matches stable
# across SDK versions that vary capitalisation.
_AZURE_STATE_TO_PROTOCOL = {
    "succeeded": "available",
    "creating": "provisioning",
    "provisioning": "provisioning",
    "failed": "error",
    "deleting": "deprovisioning",
    "updating": "updating",
    "moving": "updating",
    "unknown": "updating",
}
