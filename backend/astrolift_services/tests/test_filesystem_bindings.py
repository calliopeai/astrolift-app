from types import SimpleNamespace

import pytest
from _sdk.managed_service import VolumeMount, VolumeSourceKind

from astrolift_services.filesystem_bindings import (
    FilesystemBindingError,
    binding_resource_name,
    binding_secret_name,
    cleanup_binding_resources,
    inject_bindings_into_workloads,
    preflight_bindings,
    render_binding_storage,
    resolve_binding_secret_manifests,
)


def _binding(**overrides):
    values = {
        "guid": "019fffff-1111-7111-8111-111111111111",
        "name": "shared-data",
        "mount_path": "/mnt/shared",
        "sub_path": "",
        "source_kind": "csi",
        "protocol": "nfs4",
        "claim_name": "",
        "claim_namespace": "",
        "csi_driver": "efs.csi.aws.com",
        "volume_handle": "fs-12345678::fsap-12345678",
        "volume_attributes": {},
        "secret_refs": {},
        "secret_literals": {},
        "mount_options": ["tls"],
        "read_only": False,
        "capacity": "1Gi",
        "access_modes": ["ReadWriteMany"],
        "workload_names": [],
        "container_names": [],
        "managed_service": SimpleNamespace(
            guid="019fffff-2222-7222-8222-222222222222",
            kind="filesystem",
            name="shared",
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _deployment(*, name: str = "api", containers: list[str] | None = None):
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "acme-api"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": container, "image": "example.invalid/app:1"}
                        for container in (containers or ["api"])
                    ],
                },
            },
        },
    }


def test_sdk_volume_mount_rejects_invalid_kubernetes_name_and_capacity() -> None:
    with pytest.raises(ValueError, match="DNS label"):
        VolumeMount(
            name="Shared_Data",
            mount_path="/data",
            source_kind=VolumeSourceKind.CSI,
            protocol="nfs4",
            csi_driver="nfs.csi.k8s.io",
            volume_handle="server#/share#",
        )

    with pytest.raises(ValueError, match="cannot define the same key"):
        VolumeMount(
            name="shared-data",
            mount_path="/data",
            source_kind=VolumeSourceKind.CSI,
            protocol="smb3",
            csi_driver="file.csi.azure.com",
            volume_handle="resource-group#account#share",
            secret_refs={"azurestorageaccountname": "secret/account-name"},
            secret_literals={"azurestorageaccountname": "account-name"},
        )

    with pytest.raises(ValueError, match="Kubernetes quantity"):
        VolumeMount(
            name="shared-data",
            mount_path="/data",
            source_kind=VolumeSourceKind.CSI,
            protocol="nfs4",
            csi_driver="nfs.csi.k8s.io",
            volume_handle="server#/share#",
            capacity="1 gigabyte",
        )


def test_render_csi_binding_creates_owned_static_storage_without_plaintext() -> None:
    binding = _binding(
        csi_driver="smb.csi.k8s.io",
        protocol="smb3",
        volume_handle="files.example##share",
        volume_attributes={"source": "//files.example/share"},
        secret_refs={"username": "secret/path#username", "password": "secret/path#password"},
    )

    resources, pod_volume, mount = render_binding_storage(
        binding,
        namespace="acme-api",
        consumer_key="environment-guid",
    )

    assert [row["kind"] for row in resources] == ["PersistentVolume", "PersistentVolumeClaim"]
    pv, pvc = resources
    resource_name = binding_resource_name(binding, "environment-guid")
    assert pv["metadata"]["name"] == resource_name
    assert pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
    assert pv["spec"]["claimRef"] == {"name": resource_name, "namespace": "acme-api"}
    assert pv["spec"]["csi"]["driver"] == "smb.csi.k8s.io"
    assert pv["spec"]["csi"]["readOnly"] is False
    assert pv["spec"]["csi"]["nodeStageSecretRef"] == {
        "name": binding_secret_name(binding, "environment-guid"),
        "namespace": "acme-api",
    }
    assert "secret/path" not in str(resources)
    assert pvc["spec"]["volumeName"] == resource_name
    assert pod_volume["persistentVolumeClaim"]["claimName"] == resource_name
    assert mount == {"name": "shared-data", "mountPath": "/mnt/shared", "readOnly": False}


def test_render_existing_claim_never_creates_or_owns_storage() -> None:
    binding = _binding(
        source_kind="existing_pvc",
        protocol="pvc",
        claim_name="shared-pvc",
        claim_namespace="acme-api",
        csi_driver="",
        volume_handle="",
    )

    resources, pod_volume, _mount = render_binding_storage(
        binding,
        namespace="acme-api",
        consumer_key="environment-guid",
    )

    assert resources == []
    assert pod_volume == {
        "name": "shared-data",
        "persistentVolumeClaim": {"claimName": "shared-pvc", "readOnly": False},
    }


def test_existing_claim_cannot_cross_namespace() -> None:
    binding = _binding(
        source_kind="existing_pvc",
        protocol="pvc",
        claim_name="shared-pvc",
        claim_namespace="project-resources",
        csi_driver="",
        volume_handle="",
    )

    with pytest.raises(FilesystemBindingError, match="not consumer namespace"):
        render_binding_storage(binding, namespace="acme-api", consumer_key="environment-guid")


def test_inject_targets_named_workload_and_container() -> None:
    api = _deployment(name="api", containers=["api", "sidecar"])
    worker = _deployment(name="worker", containers=["worker"])
    binding = _binding(workload_names=["api"], container_names=["sidecar"])

    resources = inject_bindings_into_workloads(
        [api, worker],
        [binding],
        namespace="acme-api",
        consumer_key="environment-guid",
    )

    api_result = next(
        row for row in resources if row.get("kind") == "Deployment" and row["metadata"]["name"] == "api"
    )
    worker_result = next(
        row for row in resources if row.get("kind") == "Deployment" and row["metadata"]["name"] == "worker"
    )
    api_spec = api_result["spec"]["template"]["spec"]
    assert api_spec["volumes"][0]["name"] == "shared-data"
    assert "volumeMounts" not in api_spec["containers"][0]
    assert api_spec["containers"][1]["volumeMounts"][0]["mountPath"] == "/mnt/shared"
    assert "volumes" not in worker_result["spec"]["template"]["spec"]


def test_inject_rejects_mount_path_collision() -> None:
    deployment = _deployment()
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    container["volumeMounts"] = [{"name": "manifest-data", "mountPath": "/mnt/shared"}]

    with pytest.raises(FilesystemBindingError, match="mount path"):
        inject_bindings_into_workloads(
            [deployment],
            [_binding()],
            namespace="acme-api",
            consumer_key="environment-guid",
        )


def test_preflight_fails_closed_when_required_csi_driver_is_missing() -> None:
    driver = SimpleNamespace(list_csi_drivers=lambda _cluster: ["other.csi.example"])

    with pytest.raises(FilesystemBindingError, match="requires CSI driver"):
        preflight_bindings(
            [_binding()],
            cluster_driver=driver,
            cluster_slug="prod",
            namespace="acme-api",
        )


def test_preflight_rejects_cross_cluster_attachment() -> None:
    binding = _binding()
    binding.managed_service.effective_cluster = SimpleNamespace(slug="other")
    driver = SimpleNamespace(list_csi_drivers=lambda _cluster: ["efs.csi.aws.com"])

    with pytest.raises(FilesystemBindingError, match="not runtime cluster"):
        preflight_bindings(
            [binding],
            cluster_driver=driver,
            cluster_slug="prod",
            namespace="acme-api",
        )


def test_cleanup_only_returns_resources_owned_by_astrolift() -> None:
    csi = _binding(secret_refs={"username": "secret/path#username"})
    existing = _binding(
        guid="019fffff-3333-7333-8333-333333333333",
        name="operator-data",
        source_kind="existing_pvc",
        protocol="pvc",
        claim_name="operator-owned",
        claim_namespace="acme-api",
        csi_driver="",
        volume_handle="",
    )

    refs = cleanup_binding_resources(
        [csi, existing],
        namespace="acme-api",
        consumer_key="environment-guid",
    )

    assert [ref["kind"] for ref in refs] == ["Secret", "PersistentVolumeClaim", "PersistentVolume"]
    assert "operator-owned" not in str(refs)


def test_secret_resolution_errors_do_not_disclose_backend_refs_or_provider_details() -> None:
    binding = _binding(secret_refs={"password": "sensitive/provider/path#password"})

    class _FailingBackend:
        @staticmethod
        def get(_ref):
            raise RuntimeError("provider request included a sensitive diagnostic")

    with pytest.raises(FilesystemBindingError) as caught:
        resolve_binding_secret_manifests(
            [binding],
            secrets_backend=_FailingBackend(),
            namespace="acme-api",
            consumer_key="environment-guid",
        )

    assert "sensitive/provider/path" not in str(caught.value)
    assert "sensitive diagnostic" not in str(caught.value)


def test_secret_resolution_selects_bundle_fields_portably() -> None:
    binding = _binding(
        secret_refs={
            "username": "shared/files#client_user",
            "password": "shared/files#client_password",
        },
    )

    class _BundleBackend:
        calls: list[str] = []

        def get(self, ref):
            self.calls.append(ref)
            return {"client_user": "agent-user", "client_password": "agent-password"}

    backend = _BundleBackend()
    resources = resolve_binding_secret_manifests(
        [binding],
        secrets_backend=backend,
        namespace="acme-api",
        consumer_key="environment-guid",
    )

    assert backend.calls == ["shared/files", "shared/files"]
    assert resources[0]["stringData"] == {
        "password": "agent-password",
        "username": "agent-user",
    }


def test_secret_resolution_combines_non_secret_csi_identity_with_credential_ref() -> None:
    binding = _binding(
        secret_refs={"azurestorageaccountkey": "azure/files#primary"},
        secret_literals={"azurestorageaccountname": "astroliftfiles"},
    )

    backend = SimpleNamespace(get=lambda _ref: {"primary": "rotating-key"})
    resources = resolve_binding_secret_manifests(
        [binding],
        secrets_backend=backend,
        namespace="acme-api",
        consumer_key="environment-guid",
    )

    assert resources[0]["stringData"] == {
        "azurestorageaccountkey": "rotating-key",
        "azurestorageaccountname": "astroliftfiles",
    }
