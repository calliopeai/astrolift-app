from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.availability import MATRIX
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.object_store_existing_s3 import (
    ExistingS3Config,
    ExistingS3ObjectStoreDriver,
)
from k8s_native.plugin import PLUGIN


@dataclass
class _Result:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        return list(self.errors)


class _Cluster:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.applied: list[list[dict[str, Any]]] = []
        self.deleted: list[list[dict[str, Any]]] = []
        self.apply_result = _Result()
        self.delete_result = _Result()

    def get_manifest(self, cluster_id, namespace, kind, name):
        del cluster_id
        value = self.objects.get((kind, namespace, name))
        return copy.deepcopy(value) if value is not None else None

    def apply_manifests(self, cluster_id, namespace, manifests):
        del cluster_id
        self.applied.append(copy.deepcopy(manifests))
        if self.apply_result.ok:
            for manifest in manifests:
                self.objects[
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    )
                ] = copy.deepcopy(manifest)
        return self.apply_result

    def delete_manifests(self, cluster_id, namespace, manifests):
        del cluster_id
        self.deleted.append(copy.deepcopy(manifests))
        if self.delete_result.ok:
            for manifest in manifests:
                self.objects.pop(
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    ),
                    None,
                )
        return self.delete_result


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {
            "managed/object_store/acme/uploads": {
                "accessKey": "local-access-key",
                "secretKey": "local-secret-key",
            },
        }
        self.reads: list[str] = []

    def get(self, path: str):
        self.reads.append(path)
        value = self.values.get(path)
        return copy.deepcopy(value) if value is not None else None


class _FailingSecrets(_Secrets):
    def get(self, path: str):
        del path
        raise RuntimeError("backend unavailable: do not leak this connection string")


def _config(**overrides: Any) -> dict[str, Any]:
    return {
        "endpoint": "https://objects.example.test",
        "bucket_name": "acme-uploads",
        "region": "local-1",
        "prefix": "/incoming/",
        "credential_bundle": "managed/object_store/acme/uploads",
        **overrides,
    }


def _spec(**overrides: Any) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "app-1",
        "app_slug": "payments",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "uploads",
        "size": "custom",
        "config": _config(),
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(
    cluster: _Cluster | None = None,
    secrets: _Secrets | None = None,
    **policy: Any,
) -> tuple[ExistingS3ObjectStoreDriver, _Cluster, _Secrets]:
    live_cluster = cluster or _Cluster()
    live_secrets = secrets or _Secrets()
    policy.setdefault("allowed_endpoint_hosts", ("objects.example.test",))
    driver = ExistingS3ObjectStoreDriver(
        config=ExistingS3Config(
            cluster_driver=live_cluster,
            secrets_backend=live_secrets,
            **policy,
        ),
    )
    return driver, live_cluster, live_secrets


def _record(cluster: _Cluster) -> dict[str, Any]:
    return cluster.objects[("v1/ConfigMap", "acme-payments", "payments-prod-uploads-s3")]


def _document(cluster: _Cluster) -> dict[str, Any]:
    return json.loads(_record(cluster)["data"]["connection.json"])


def test_provision_registers_nonsecret_connection_record_and_is_immediately_ready() -> None:
    driver, cluster, secrets = _driver()

    result = driver.provision(_spec())

    assert result.ok is True
    assert result.ready is True
    assert result.handle == "object_store/cluster-1/acme-payments/payments-prod-uploads-s3"
    assert "reachability was not probed" in result.message
    record = _record(cluster)
    assert record["metadata"]["labels"] == {
        "app.kubernetes.io/managed-by": "astrolift",
        "astrolift.io/managed-service-id": "service-1",
        "astrolift.io/component": "s3-compatible-registration",
        "astrolift.io/organization": "acme",
        "astrolift.io/app": "payments",
        "astrolift.io/environment": "prod",
    }
    document = _document(cluster)
    assert document == {
        "version": 1,
        "endpoint": "https://objects.example.test",
        "bucket_name": "acme-uploads",
        "region": "local-1",
        "prefix": "incoming",
        "path_style": True,
        "tls_verify": True,
        "credential_bundle": "managed/object_store/acme/uploads",
        "access_key_field": "accessKey",
        "secret_key_field": "secretKey",
        "session_token_field": "",
        "deletion_protection": False,
    }
    serialized = json.dumps(record)
    assert "local-access-key" not in serialized
    assert "local-secret-key" not in serialized
    assert secrets.reads == ["managed/object_store/acme/uploads"]


def test_binding_uses_secret_references_and_optional_session_token() -> None:
    secrets = _Secrets()
    secrets.values["managed/object_store/acme/uploads"]["session"] = "temporary-session-token"
    driver, _, _ = _driver(secrets=secrets)
    provisioned = driver.provision(
        _spec(config=_config(path_style=False, session_token_field="session")),
    )

    binding = driver.binding(ServiceHandle(provisioned.handle))

    assert binding.env_vars["BUCKET_NAME"].literal == "acme-uploads"
    assert binding.env_vars["BUCKET_PREFIX"].literal == "incoming"
    assert binding.env_vars["AWS_ACCESS_KEY_ID"].secret_ref == ("managed/object_store/acme/uploads#accessKey")
    assert binding.env_vars["AWS_SECRET_ACCESS_KEY"].secret_ref == ("managed/object_store/acme/uploads#secretKey")
    assert binding.env_vars["AWS_SESSION_TOKEN"].secret_ref == ("managed/object_store/acme/uploads#session")
    assert binding.env_vars["AWS_S3_FORCE_PATH_STYLE"].literal == "false"
    assert binding.env_vars["S3_TLS_VERIFY"].literal == "true"
    assert "temporary-session-token" not in repr(binding)


@pytest.mark.parametrize(
    ("config", "policy", "message"),
    [
        (_config(endpoint="https://objects.example.test.evil.invalid"), {}, "outside the cluster allowlist"),
        (_config(endpoint="http://objects.example.test"), {}, "plain HTTP"),
        (
            _config(endpoint="https://objects.example.test", tls_verify=False),
            {},
            "TLS verification",
        ),
        (_config(endpoint="https://user:pass@objects.example.test"), {}, "cannot embed credentials"),
        (_config(endpoint="https://objects.example.test?token=secret"), {}, "query strings"),
        (_config(endpoint="https://objects.example.test/base"), {}, "URL paths"),
        (_config(path_style="true"), {}, "path_style must be a boolean"),
        (_config(bucket_name="bad/bucket"), {}, "bucket_name"),
        (_config(region="bad region"), {}, "region"),
        (
            _config(credential_bundle="managed/other/credentials"),
            {},
            "outside the cluster path allowlist",
        ),
        (
            _config(credential_bundle="managed/object_store/other-org/uploads"),
            {},
            "outside the cluster path allowlist",
        ),
        (
            _config(credential_bundle="managed/object_store/../admin"),
            {},
            "canonical secret path",
        ),
        (_config(access_key_field="same", secret_key_field="same"), {}, "fields must differ"),
        (_config(unknown=True), {}, "unsupported fields"),
    ],
)
def test_connection_and_secret_reference_policy_fails_closed(
    config: dict[str, Any],
    policy: dict[str, Any],
    message: str,
) -> None:
    driver, _, _ = _driver(**policy)

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert message in result.message


def test_internal_service_http_and_explicit_endpoint_relaxations_are_policy_gated() -> None:
    driver, cluster, _ = _driver(
        allowed_endpoint_hosts=("storage.svc.cluster.local",),
        allow_insecure_http=True,
        allow_skip_tls_verify=True,
        allow_endpoint_paths=True,
    )

    internal = driver.provision(
        _spec(
            config=_config(
                endpoint="http://minio.storage.svc.cluster.local:9000/base/",
                tls_verify=False,
            ),
        ),
    )

    assert internal.ok is True
    assert _document(cluster)["endpoint"] == "http://minio.storage.svc.cluster.local:9000/base"
    assert _document(cluster)["tls_verify"] is False


def test_cluster_service_names_are_not_implicitly_trusted() -> None:
    driver, _, _ = _driver(allowed_endpoint_hosts=())

    result = driver.provision(
        _spec(config=_config(endpoint="https://minio.storage.svc.cluster.local")),
    )

    assert result.ok is False
    assert "outside the cluster allowlist" in result.message


def test_external_host_suffix_allowlist_uses_dns_boundary() -> None:
    allowed, _, _ = _driver(allowed_endpoint_hosts=("storage.example.test",))
    denied, _, _ = _driver(allowed_endpoint_hosts=("storage.example.test",))

    assert (
        allowed.provision(
            _spec(config=_config(endpoint="https://tenant.storage.example.test")),
        ).ok
        is True
    )
    assert (
        denied.provision(
            _spec(config=_config(endpoint="https://storage.example.test.evil.invalid")),
        ).ok
        is False
    )


def test_missing_or_unreadable_credential_bundle_fails_without_leaking_backend_error() -> None:
    missing_secrets = _Secrets()
    missing_secrets.values.clear()
    missing, _, _ = _driver(secrets=missing_secrets)
    unreadable, _, _ = _driver(secrets=_FailingSecrets())

    no_bundle = missing.provision(_spec())
    backend_error = unreadable.provision(_spec())

    assert no_bundle.ok is False
    assert "does not exist" in no_bundle.message
    assert backend_error.ok is False
    assert backend_error.message == "existing S3 credential bundle could not be read"
    assert "connection string" not in backend_error.message


@pytest.mark.parametrize("field", ["accessKey", "secretKey", "session"])
def test_required_credential_fields_must_be_nonempty(field: str) -> None:
    secrets = _Secrets()
    secrets.values["managed/object_store/acme/uploads"][field] = ""
    config = _config(session_token_field="session") if field == "session" else _config()
    driver, _, _ = _driver(secrets=secrets)

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert "missing" in result.message


def test_provision_is_idempotent_for_same_owner_and_rejects_foreign_records() -> None:
    driver, cluster, _ = _driver()

    first = driver.provision(_spec())
    second = driver.provision(_spec())
    _record(cluster)["metadata"]["labels"]["astrolift.io/managed-service-id"] = "other-service"
    foreign = driver.provision(_spec())

    assert first.ok and second.ok
    assert foreign.ok is False
    assert "another Astrolift resource" in foreign.message

    _record(cluster)["metadata"]["labels"] = {"app": "foreign"}
    collision = driver.provision(_spec())
    assert collision.ok is False
    assert "foreign ConfigMap" in collision.message


def test_update_reconciles_connection_without_changing_owner() -> None:
    driver, cluster, _ = _driver()
    provisioned = driver.provision(_spec())

    updated = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config=_config(bucket_name="acme-archive", prefix="archive"),
        ),
    )

    assert updated.ok is True
    assert _document(cluster)["bucket_name"] == "acme-archive"
    assert _document(cluster)["prefix"] == "archive"
    assert _record(cluster)["metadata"]["labels"]["astrolift.io/managed-service-id"] == "service-1"


def test_status_fails_closed_for_record_tampering_and_rotated_credential_removal() -> None:
    driver, cluster, secrets = _driver()
    provisioned = driver.provision(_spec())

    ready = driver.status(ServiceHandle(provisioned.handle))
    secrets.values.clear()
    missing_secret = driver.status(ServiceHandle(provisioned.handle))
    _record(cluster)["data"]["connection.json"] = "not-json"
    malformed = driver.status(ServiceHandle(provisioned.handle))

    assert ready.state == "available"
    assert "reachability is not probed" in ready.message
    assert missing_secret.state == "error"
    assert "does not exist" in missing_secret.message
    assert malformed.state == "error"
    assert "malformed connection data" in malformed.message


def test_deprovision_never_deletes_external_data_or_credentials() -> None:
    driver, cluster, secrets = _driver()
    provisioned = driver.provision(_spec(config=_config(deletion_protection=True)))

    data_delete = driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    protected = driver.deprovision(DeprovisionSpec(provisioned.handle))
    unlinked = driver.deprovision(DeprovisionSpec(provisioned.handle), force_destroy=True)
    repeated = driver.deprovision(DeprovisionSpec(provisioned.handle), force_destroy=True)

    assert data_delete.ok is False
    assert data_delete.retryable is False
    assert data_delete.errors == ["external_data_deletion_unsupported"]
    assert protected.ok is False
    assert protected.errors == ["deletion_protection_enabled"]
    assert unlinked.ok is True
    assert "bucket, objects, and credential bundle were retained" in unlinked.message
    assert repeated.ok is True
    assert secrets.values["managed/object_store/acme/uploads"]["accessKey"] == "local-access-key"
    assert len(cluster.deleted) == 1


def test_policy_tightening_does_not_trap_an_owned_registration() -> None:
    driver, cluster, secrets = _driver()
    provisioned = driver.provision(_spec())
    tightened = ExistingS3ObjectStoreDriver(
        config=ExistingS3Config(
            cluster_driver=cluster,
            secrets_backend=secrets,
            allowed_endpoint_hosts=(),
            allowed_credential_path_prefixes=(),
        ),
    )

    status = tightened.status(ServiceHandle(provisioned.handle))
    unlinked = tightened.deprovision(DeprovisionSpec(provisioned.handle))

    assert status.state == "error"
    assert "outside the cluster allowlist" in status.message
    assert unlinked.ok is True
    assert ("v1/ConfigMap", "acme-payments", "payments-prod-uploads-s3") not in cluster.objects


def test_apply_and_delete_failures_are_reported() -> None:
    driver, cluster, _ = _driver()
    cluster.apply_result = _Result(False, ["admission denied"])

    rejected = driver.provision(_spec())

    assert rejected.ok is False
    assert rejected.errors == ["admission denied"]

    cluster.apply_result = _Result()
    provisioned = driver.provision(_spec())
    cluster.delete_result = _Result(False, ["apiserver unavailable"])
    failed_delete = driver.deprovision(DeprovisionSpec(provisioned.handle))
    assert failed_delete.ok is False
    assert failed_delete.errors == ["apiserver unavailable"]


def test_snapshot_restore_and_legacy_handles_are_explicitly_unsupported() -> None:
    driver, _, _ = _driver()

    with pytest.raises(UnsupportedOperationError, match="external store backup"):
        driver.snapshot(ServiceHandle("object_store/cluster/ns/name"))
    with pytest.raises(UnsupportedOperationError, match="external store operator"):
        driver.restore(None, _spec())  # type: ignore[arg-type]
    assert driver.status(ServiceHandle("object_store/legacy")).state == "error"


def test_plugin_catalog_and_schemas_expose_executable_preview() -> None:
    driver, _, _ = _driver()
    entry = next(
        row
        for row in MATRIX.managed_services
        if row.plugin_id == "k8s_native" and row.variant == "s3_compatible_existing"
    )

    assert PLUGIN.managed_service_drivers[("object_store", "s3_compatible_existing")] is (ExistingS3ObjectStoreDriver)
    assert entry.status == "preview"
    assert "AWS_ACCESS_KEY_ID" in entry.binding_envs
    assert driver.config_schema()["required"] == ["endpoint", "bucket_name", "credential_bundle"]
    provisioned = driver.provision(_spec())
    runtime_vars = set(driver.binding(ServiceHandle(provisioned.handle)).env_vars)
    schema_vars = set(driver.binding_schema().env_vars)
    assert schema_vars == set(entry.binding_envs)
    assert runtime_vars == schema_vars - {"AWS_SESSION_TOKEN"}
    assert "credential_bundle" in driver.editable_fields()
