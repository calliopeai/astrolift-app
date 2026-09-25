from __future__ import annotations

import base64
import copy

import pytest

from _sdk.cluster import ApplyResult, DeleteResult
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.object_store_seaweedfs import (
    _REQUIRED_CRDS,
    SeaweedFSObjectStoreConfig,
    SeaweedFSObjectStoreDriver,
)
from k8s_native.plugin import PLUGIN


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}
        self.upserts: list[tuple[str, dict[str, str]]] = []
        self.deletes: list[str] = []

    def get(self, path: str):
        value = self.values.get(path)
        return copy.deepcopy(value) if value is not None else None

    def upsert(self, path: str, kvs: dict[str, str]) -> None:
        self.values[path] = dict(kvs)
        self.upserts.append((path, dict(kvs)))

    def delete(self, path: str) -> None:
        self.values.pop(path, None)
        self.deletes.append(path)


class _FailingSecrets(_Secrets):
    def get(self, path: str):
        del path
        raise RuntimeError("backend unavailable with sensitive connection detail")


class _Cluster:
    def __init__(self, *, seaweed: bool = True, crds: bool = True) -> None:
        self.objects: dict[tuple[str, str | None, str], dict] = {}
        self.applied: list[list[dict]] = []
        self.deleted: list[list[dict]] = []
        self.block_bucket_deletion = False
        if crds:
            for name in _REQUIRED_CRDS:
                self.objects[("apiextensions.k8s.io/v1/CustomResourceDefinition", None, name)] = {
                    "apiVersion": "apiextensions.k8s.io/v1",
                    "kind": "CustomResourceDefinition",
                    "metadata": {"name": name},
                }
        if seaweed:
            self.objects[("seaweed.seaweedfs.com/v1/Seaweed", "storage", "shared")] = {
                "apiVersion": "seaweed.seaweedfs.com/v1",
                "kind": "Seaweed",
                "metadata": {"name": "shared", "namespace": "storage"},
                "spec": {"s3": {"enabled": True}},
            }

    def get_manifest(self, _cluster, namespace, kind, name):
        value = self.objects.get((kind, namespace, name))
        return copy.deepcopy(value) if value is not None else None

    def apply_manifests(self, _cluster, namespace, manifests):
        self.applied.append(copy.deepcopy(manifests))
        for manifest in manifests:
            metadata = manifest["metadata"]
            manifest_namespace = metadata.get("namespace", namespace)
            kind = f"{manifest['apiVersion']}/{manifest['kind']}"
            key = (kind, manifest_namespace, metadata["name"])
            current_status = copy.deepcopy((self.objects.get(key) or {}).get("status"))
            self.objects[key] = copy.deepcopy(manifest)
            # The Kubernetes status subresource is not replaced by a
            # server-side apply against spec/metadata.
            if current_status is not None and "status" not in manifest:
                self.objects[key]["status"] = current_status
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])

    def delete_manifests(self, _cluster, namespace, manifests):
        self.deleted.append(copy.deepcopy(manifests))
        for manifest in manifests:
            if manifest["kind"] == "Bucket" and self.block_bucket_deletion:
                continue
            metadata = manifest["metadata"]
            manifest_namespace = metadata.get("namespace", namespace)
            kind = f"{manifest['apiVersion']}/{manifest['kind']}"
            self.objects.pop((kind, manifest_namespace, metadata["name"]), None)
        return DeleteResult(deleted=[], not_found=[], errors=[])


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "project-1",
        "app_slug": "payments",
        "environment_id": "cluster-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "uploads",
        "size": "small",
        "config": {},
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(
    *,
    cluster: _Cluster | None = None,
    secrets: _Secrets | None = None,
    **config,
) -> tuple[SeaweedFSObjectStoreDriver, _Cluster, _Secrets]:
    cluster = cluster or _Cluster()
    secrets = secrets or _Secrets()
    return (
        SeaweedFSObjectStoreDriver(
            config=SeaweedFSObjectStoreConfig(
                namespace="storage",
                seaweed_name="shared",
                deletion_timeout_seconds=0,
                deletion_poll_seconds=0,
                cluster_driver=cluster,
                secrets_backend=secrets,
                **config,
            )
        ),
        cluster,
        secrets,
    )


def _mark_operator_ready(
    cluster: _Cluster,
    bucket_name: str,
    *,
    access_key: str = "ASTROLIFTSEAWEEDKEY",
    secret_key: str = "operator-generated-secret-key-material",
) -> dict[str, str]:
    for key, value in list(cluster.objects.items()):
        if key[0].startswith("seaweed.seaweedfs.com/v1/") and key[0] != "seaweed.seaweedfs.com/v1/Seaweed":
            value["status"] = {"phase": "Ready"}
    credentials = {"accessKey": access_key, "secretKey": secret_key}
    cluster.objects[("v1/Secret", "storage", f"{bucket_name}-credentials")] = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": f"{bucket_name}-credentials", "namespace": "storage"},
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in credentials.items()},
    }
    return credentials


def test_provision_emits_bucket_scoped_iam_and_operator_owned_secret_reference() -> None:
    driver, cluster, backend = _driver()

    result = driver.provision(
        _spec(
            config={
                "versioning": "Enabled",
                "quota_size": "100Gi",
                "placement": {"replication": "010", "disk_type": "ssd", "fsync": True},
            },
        )
    )

    assert result.ok is True
    assert result.ready is False
    assert result.handle.startswith("object_store/cluster-1/storage/")
    manifests = cluster.applied[-1]
    by_kind = {row["kind"]: row for row in manifests}
    assert set(by_kind) == {"S3Identity", "S3Credentials", "S3Policy", "S3PolicyBinding", "Bucket"}
    assert by_kind["Bucket"]["spec"]["reclaimPolicy"] == "Retain"
    assert by_kind["Bucket"]["spec"]["anonymousRead"] is False
    assert by_kind["Bucket"]["spec"]["quota"] == {"size": "100Gi", "enforce": True}
    assert by_kind["Bucket"]["spec"]["placement"] == {
        "replication": "010",
        "diskType": "ssd",
        "fsync": True,
    }
    statements = by_kind["S3Policy"]["spec"]["statements"]
    assert statements[0]["actions"] == ["s3:GetBucketLocation", "s3:ListBucket"]
    assert statements[1]["actions"] == ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    assert by_kind["S3Credentials"]["spec"]["secretRef"] == {
        "name": f"{by_kind['Bucket']['metadata']['name']}-credentials",
        "accessKeyField": "accessKey",
        "secretKeyField": "secretKey",
    }
    assert backend.upserts == []


def test_status_syncs_operator_credentials_idempotently_and_binding_never_returns_plaintext() -> None:
    backend = _Secrets()
    driver, cluster, backend = _driver(secrets=backend)
    first = driver.provision(_spec(config={"bucket_name": "acme-existing-bucket"}))
    original = _mark_operator_ready(cluster, "acme-existing-bucket")
    ready = driver.status(ServiceHandle(first.handle))
    path = next(iter(backend.values))

    second = driver.provision(_spec(config={"bucket_name": "acme-existing-bucket"}))
    ready_again = driver.status(ServiceHandle(second.handle))
    binding = driver.binding(ServiceHandle(second.handle), {"prefix": "/incoming/"})

    assert first.ok and second.ok
    assert ready.state == ready_again.state == "available"
    assert len(backend.upserts) == 1
    assert backend.values[path] == original
    assert binding.env_vars["AWS_ACCESS_KEY_ID"].secret_ref == f"{path}#accessKey"
    assert binding.env_vars["AWS_SECRET_ACCESS_KEY"].secret_ref == f"{path}#secretKey"
    assert binding.env_vars["BUCKET_PREFIX"].literal == "incoming"
    assert binding.env_vars["AWS_S3_FORCE_PATH_STYLE"].literal == "true"
    assert original["accessKey"] not in repr(binding)
    assert original["secretKey"] not in repr(binding)


def test_preflight_fails_before_secret_write_when_operator_or_crds_are_missing() -> None:
    missing_cluster, _, backend = _driver(cluster=_Cluster(seaweed=False))
    missing_crd_cluster = _Cluster()
    missing_crd_cluster.objects.pop(
        ("apiextensions.k8s.io/v1/CustomResourceDefinition", None, _REQUIRED_CRDS[0]),
    )
    missing_crd, _, missing_crd_backend = _driver(cluster=missing_crd_cluster)

    no_cluster = missing_cluster.provision(_spec())
    no_crd = missing_crd.provision(_spec())

    assert no_cluster.ok is False
    assert "does not exist" in no_cluster.message
    assert backend.upserts == []
    assert no_crd.ok is False
    assert _REQUIRED_CRDS[0] in no_crd.message
    assert missing_crd_backend.upserts == []


@pytest.mark.parametrize(
    "install_config",
    [
        {"endpoint": "https://user:password@objects.example.test"},
        {"endpoint": "javascript:alert(1)"},
        {"endpoint_port": 70000},
        {"credential_path_prefix": "managed/../escape"},
    ],
)
def test_invalid_install_options_fail_before_secret_or_cluster_mutation(install_config) -> None:
    driver, cluster, backend = _driver(**install_config)

    result = driver.provision(_spec())

    assert result.ok is False
    assert cluster.applied == []
    assert backend.upserts == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"object_lock": True, "versioning": "Suspended"}, "object_lock"),
        ({"quota_size": "all-the-bytes"}, "quota_size"),
        ({"placement": {"replication": "triple"}}, "replication"),
        ({"placement": {"ttl": "soon"}}, "ttl"),
        ({"placement": {"unknown": True}}, "unsupported"),
        ({"anonynous_read": True}, "unsupported"),
        ({"bucket_name": "192.168.1.1"}, "S3 DNS"),
    ],
)
def test_invalid_bucket_options_fail_closed(config, message) -> None:
    driver, cluster, backend = _driver()

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert message in result.message
    assert cluster.applied == []
    assert backend.upserts == []


def test_untrusted_names_are_sanitized_before_becoming_kubernetes_labels() -> None:
    driver, cluster, _ = _driver()

    result = driver.provision(
        _spec(
            organization_slug="ACME / clinical",
            app_slug="Bug Triage!!!",
            environment_name="Production West",
        )
    )

    assert result.ok is True
    labels = cluster.applied[-1][-1]["metadata"]["labels"]
    assert labels["astrolift.io/organization"] == "ACME-clinical"
    assert labels["astrolift.io/app"] == "Bug-Triage"
    assert labels["astrolift.io/environment"] == "Production-West"


def test_status_waits_for_every_bucket_and_iam_resource() -> None:
    driver, cluster, backend = _driver()
    result = driver.provision(_spec(config={"bucket_name": "acme-status-bucket"}))

    pending = driver.status(ServiceHandle(result.handle))
    credentials = _mark_operator_ready(cluster, "acme-status-bucket")
    ready = driver.status(ServiceHandle(result.handle))

    assert pending.state == "provisioning"
    assert ready.state == "available"
    assert backend.values["managed/object_store/storage/acme-status-bucket/credentials"] == credentials


def test_ready_iam_waits_for_operator_secret_and_rejects_malformed_material() -> None:
    driver, cluster, backend = _driver()
    result = driver.provision(_spec(config={"bucket_name": "acme-secret-bucket"}))
    _mark_operator_ready(cluster, "acme-secret-bucket")
    secret_key = ("v1/Secret", "storage", "acme-secret-bucket-credentials")
    secret = cluster.objects.pop(secret_key)

    missing = driver.status(ServiceHandle(result.handle))
    cluster.objects[secret_key] = secret
    cluster.objects[secret_key]["data"]["secretKey"] = "not-base64!"
    malformed = driver.status(ServiceHandle(result.handle))

    assert missing.state == "provisioning"
    assert malformed.state == "error"
    assert "malformed" in malformed.message
    assert backend.upserts == []


def test_secret_backend_failure_is_a_safe_terminal_status() -> None:
    driver, cluster, _ = _driver(secrets=_FailingSecrets())
    result = driver.provision(_spec(config={"bucket_name": "acme-secret-backend"}))
    _mark_operator_ready(cluster, "acme-secret-backend")

    status = driver.status(ServiceHandle(result.handle))

    assert status.state == "error"
    assert status.message == ("SeaweedFS credentials could not be synchronized to the external secrets backend")
    assert "sensitive" not in status.message


def test_failed_operator_condition_is_exposed_without_credentials() -> None:
    driver, cluster, _ = _driver()
    result = driver.provision(_spec(config={"bucket_name": "acme-failed-bucket"}))
    bucket_key = ("seaweed.seaweedfs.com/v1/Bucket", "storage", "acme-failed-bucket")
    cluster.objects[bucket_key]["status"] = {
        "phase": "Failed",
        "conditions": [{"type": "Ready", "status": "False", "message": "owner identity missing"}],
    }

    status = driver.status(ServiceHandle(result.handle))

    assert status.state == "error"
    assert status.message == "owner identity missing"


def test_update_preserves_identity_and_applies_mutable_bucket_fields() -> None:
    driver, cluster, _ = _driver()
    result = driver.provision(_spec(config={"bucket_name": "acme-update-bucket"}))

    updated = driver.update(
        UpdateSpec(
            handle=result.handle,
            config={"versioning": "Suspended", "anonymous_read": True, "quota_size": "5Gi"},
        )
    )

    assert updated.ok is True
    manifest = cluster.applied[-1][0]
    assert manifest["spec"]["owner"] == "acme-update-bucket-identity"
    assert manifest["spec"]["versioning"] == "Suspended"
    assert manifest["spec"]["anonymousRead"] is True
    assert manifest["spec"]["quota"] == {"size": "5Gi", "enforce": True}
    assert "status" not in manifest
    assert "resourceVersion" not in manifest["metadata"]


@pytest.mark.parametrize(
    ("delete_data", "policy", "bucket_deleted"),
    [(False, "Retain", False), (True, "Delete", True)],
)
def test_deprovision_sets_reclaim_policy_then_revokes_iam_and_secret(
    delete_data,
    policy,
    bucket_deleted,
) -> None:
    driver, cluster, backend = _driver()
    result = driver.provision(_spec(config={"bucket_name": "acme-delete-bucket"}))
    _mark_operator_ready(cluster, "acme-delete-bucket")
    assert driver.status(ServiceHandle(result.handle)).state == "available"
    credential_path = next(iter(backend.values))

    deleted = driver.deprovision(DeprovisionSpec(result.handle), delete_data=delete_data)

    assert deleted.ok is True
    bucket_reconciles = [manifest for batch in cluster.applied for manifest in batch if manifest["kind"] == "Bucket"]
    assert bucket_reconciles[-1]["spec"]["reclaimPolicy"] == policy
    if not delete_data:
        assert bucket_reconciles[-1]["spec"]["owner"] == ""
        assert bucket_reconciles[-1]["spec"]["access"] == []
        assert bucket_reconciles[-1]["spec"]["anonymousRead"] is False
    assert backend.deletes == [credential_path]
    deleted_kinds = [manifest["kind"] for batch in cluster.deleted for manifest in batch]
    expected = [
        "S3PolicyBinding",
        "S3Credentials",
        "S3Policy",
        "S3Identity",
        "Secret",
    ]
    if bucket_deleted:
        expected.insert(0, "Bucket")
    assert deleted_kinds == expected


def test_retained_bucket_can_be_rebound_without_adopting_a_foreign_bucket() -> None:
    driver, cluster, backend = _driver()
    spec = _spec(config={"bucket_name": "acme-retained-bucket"})
    first = driver.provision(spec)
    _mark_operator_ready(cluster, "acme-retained-bucket")
    assert driver.status(ServiceHandle(first.handle)).state == "available"

    retained = driver.deprovision(DeprovisionSpec(first.handle), delete_data=False)
    rebound = driver.provision(spec)
    rotated = _mark_operator_ready(
        cluster,
        "acme-retained-bucket",
        access_key="ASTROLIFTSEAWEEDNEW",
        secret_key="operator-rotated-secret-key-material",
    )
    assert driver.status(ServiceHandle(rebound.handle)).state == "available"

    assert retained.ok is True
    assert rebound.ok is True
    bucket_key = ("seaweed.seaweedfs.com/v1/Bucket", "storage", "acme-retained-bucket")
    assert cluster.objects[bucket_key]["spec"]["owner"] == "acme-retained-bucket-identity"
    assert cluster.objects[bucket_key]["spec"]["adoptExisting"] is False
    assert len(backend.upserts) == 2
    assert backend.values["managed/object_store/storage/acme-retained-bucket/credentials"] == rotated


def test_object_lock_retention_stops_teardown_before_iam_revocation() -> None:
    driver, cluster, backend = _driver()
    result = driver.provision(_spec(config={"bucket_name": "acme-locked-bucket", "object_lock": True}))
    bucket_key = ("seaweed.seaweedfs.com/v1/Bucket", "storage", "acme-locked-bucket")
    cluster.objects[bucket_key]["status"] = {
        "phase": "Terminating",
        "conditions": [{"type": "DeleteBlockedByRetention", "status": "True"}],
    }
    cluster.block_bucket_deletion = True

    deleted = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True, force_destroy=True)

    assert deleted.ok is False
    assert deleted.retryable is False
    assert deleted.errors == ["object_lock_retention_active"]
    assert backend.deletes == []
    assert [manifest["kind"] for batch in cluster.deleted for manifest in batch] == ["Bucket"]


def test_plugin_registers_preview_seaweedfs_driver() -> None:
    assert PLUGIN.managed_service_drivers[("object_store", "seaweedfs_operator")] is SeaweedFSObjectStoreDriver


def test_a_tenant_bucket_name_cannot_take_another_services_bucket() -> None:
    """bucket_name is tenant-set and every bucket shares one namespace (#1959)."""
    driver, cluster, _ = _driver()
    first = driver.provision(_spec(config={"bucket_name": "shared-assets"}))
    applied = len(cluster.applied)

    other = _spec(organization_slug="globex", managed_service_id="service-2", config={"bucket_name": "shared-assets"})
    second = driver.provision(other)

    assert first.ok, first.message
    assert not second.ok and "refusing to adopt" in second.message
    assert len(cluster.applied) == applied


def test_reprovisioning_this_services_bucket_still_reconciles() -> None:
    driver, _, _ = _driver()
    assert driver.provision(_spec()).ok
    again = driver.provision(_spec())
    assert again.ok, again.message
