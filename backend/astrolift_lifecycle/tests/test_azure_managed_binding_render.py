"""Azure managed-service secrets reach workload bindings without ambiguity."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_services.models import ManagedService, ManagedServiceBinding
from astrolift_workflows.activities.app_lifecycle import _update_secrets_sync
from core.app_deploy import AppDeployError
from providers._sdk.cluster import ApplyResult
from providers.azure.secrets_keyvault import KeyVaultConfig, KeyVaultSecretsBackend

pytestmark = pytest.mark.django_db


class _SecretClient:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.reads: list[str] = []

    def get_secret(self, name: str) -> SimpleNamespace:
        self.reads.append(name)
        return SimpleNamespace(value=self.values[name])


class _ClusterDriver:
    def __init__(self) -> None:
        self.applied: list[dict[str, object]] = []

    def apply_manifests(
        self,
        cluster_slug: str,
        namespace: str,
        manifests: list[dict[str, object]],
    ) -> ApplyResult:
        assert cluster_slug == "azure-prod"
        assert namespace == "apps"
        self.applied = manifests
        return ApplyResult(
            created=["Secret/astrolift-bindings-hello-app"], updated=[], unchanged=[], errors=[]
        )


def _deployment(app, env) -> Deployment:
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.PENDING,
        image_tag="managed-binding-test",
    )


def _binding(
    app,
    env,
    *,
    secret_ref: str,
    bind_workloads: list[str] | None = None,
) -> ManagedServiceBinding:
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="orders",
        variant="postgres_flexible",
        status=ManagedService.Status.ACTIVE,
        bind_workloads=bind_workloads or [],
    )
    return ManagedServiceBinding.objects.create(
        managed_service=service,
        env_key="DATABASE_PASSWORD",
        env_value_ref=secret_ref,
        is_secret=True,
    )


def _install_drivers(monkeypatch, *, secret_value: str) -> tuple[_SecretClient, _ClusterDriver]:
    secret_client = _SecretClient({"astrolift-pg-orders-master": secret_value})
    secrets_backend = KeyVaultSecretsBackend(
        config=KeyVaultConfig(
            vault_url="https://platform.vault.azure.net",
            client=secret_client,
        ),
    )
    cluster_driver = _ClusterDriver()

    def _capability_driver(cluster, capability: str):  # noqa: ANN001
        assert capability == "secrets"
        return secrets_backend

    monkeypatch.setattr("core.app_deploy.driver_for_capability", _capability_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda deployment: (cluster_driver, SimpleNamespace(slug="azure-prod"), "apps"),
    )
    return secret_client, cluster_driver


def test_explicit_key_vault_binding_materializes_into_workload_secret(app, env, monkeypatch) -> None:
    secret_ref = "azure-kv://platform.vault.azure.net/secrets/astrolift-pg-orders-master"
    binding = _binding(app, env, secret_ref=secret_ref)
    deployment = _deployment(app, env)
    secret_client, cluster_driver = _install_drivers(monkeypatch, secret_value="correct-horse")

    assert _update_secrets_sync(deployment.pk) == 1

    assert secret_client.reads == ["astrolift-pg-orders-master"]
    assert len(cluster_driver.applied) == 1
    rendered = cluster_driver.applied[0]
    assert rendered["kind"] == "Secret"
    assert rendered["metadata"]["name"] == "astrolift-bindings-hello-app"  # type: ignore[index]
    assert base64.b64decode(rendered["data"]["DATABASE_PASSWORD"]).decode() == "correct-horse"  # type: ignore[index]
    binding.refresh_from_db()
    assert binding.env_value_ref == secret_ref
    assert binding.env_value_ref != "correct-horse"


def test_empty_managed_secret_value_fails_closed(app, env, monkeypatch) -> None:
    secret_ref = "azure-kv://platform.vault.azure.net/secrets/astrolift-pg-orders-master"
    _binding(app, env, secret_ref=secret_ref)
    deployment = _deployment(app, env)
    _secret_client, cluster_driver = _install_drivers(monkeypatch, secret_value="")

    with pytest.raises(AppDeployError, match="resolved to an empty secret value"):
        _update_secrets_sync(deployment.pk)

    assert cluster_driver.applied == []


def test_workload_selector_materializes_only_the_scoped_secret(app, env, monkeypatch) -> None:
    secret_ref = "azure-kv://platform.vault.azure.net/secrets/astrolift-pg-orders-master"
    _binding(app, env, secret_ref=secret_ref, bind_workloads=["api"])
    deployment = _deployment(app, env)
    _secret_client, cluster_driver = _install_drivers(monkeypatch, secret_value="scoped-value")

    assert _update_secrets_sync(deployment.pk) == 1

    assert [row["metadata"]["name"] for row in cluster_driver.applied] == ["astrolift-bindings-hello-app-api"]
