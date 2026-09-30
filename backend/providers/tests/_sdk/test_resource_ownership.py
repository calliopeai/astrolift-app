"""Private resource ownership survives create/delete races on every cloud."""

from __future__ import annotations

import importlib
from unittest.mock import Mock

import pytest

from _sdk.k8s_dynamic_client import AlreadyExistsError, KubernetesDynamicClient, PreconditionFailedError

DRIVERS = [
    ("aws.cluster_eks", "EKSClusterDriver"),
    ("gcp.cluster_gke", "GKEClusterDriver"),
    ("azure.cluster_aks", "AKSClusterDriver"),
    ("k8s_native.cluster", "K8sNativeClusterDriver"),
]


@pytest.fixture(params=DRIVERS, ids=[entry[0] for entry in DRIVERS])
def driver(request, monkeypatch):
    module, name = request.param
    cls = getattr(importlib.import_module(module), name)
    instance = object.__new__(cls)
    client = Mock()
    client.create_manifest.return_value = "created"
    client.server_side_apply.return_value = "updated"
    monkeypatch.setattr(instance, "_k8s", lambda _cluster: client)
    return instance, client


def manifest(name="private", **metadata):
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, **metadata}}


def test_create_only_never_reads_or_applies_existing_objects(driver):
    instance, client = driver
    body = manifest()
    result = instance.apply_manifests("cluster", "tenant", [body], create_only=True, dry_run=True)
    assert result.ok and result.created == ["Secret/private"]
    client.create_manifest.assert_called_once_with(namespace="tenant", manifest=body, dry_run=True)
    client.server_side_apply.assert_not_called()
    client.get.assert_not_called()


def test_conflict_refuses_adoption_and_prevents_later_job_creation(driver):
    instance, client = driver
    client.create_manifest.side_effect = ["created", AlreadyExistsError("occupied name"), "created"]
    result = instance.apply_manifests(
        "cluster", "tenant", [manifest("first"), manifest("foreign"), manifest("job")], create_only=True
    )
    assert not result.ok
    assert result.created == ["Secret/first"]
    assert not result.updated and not result.unchanged
    assert len(result.errors) == 1 and not result.errors[0].is_retryable
    assert result.errors[0].exception_type == "AlreadyExistsError"
    assert client.create_manifest.call_count == 2
    client.server_side_apply.assert_not_called()


def test_normal_apply_remains_update_capable(driver):
    instance, client = driver
    body = manifest()
    result = instance.apply_manifests("cluster", "tenant", [body])
    assert result.ok and result.updated == ["Secret/private"]
    client.server_side_apply.assert_called_once_with(namespace="tenant", manifest=body, dry_run=False)
    client.create_manifest.assert_not_called()


def test_delete_preserves_both_identity_preconditions_and_propagation(driver):
    instance, client = driver
    result = instance.delete_manifests(
        "cluster", "tenant", [manifest(uid="original-uid", resourceVersion="42")], propagation_policy="Foreground"
    )
    assert result.ok
    client.delete.assert_called_once_with(
        kind="Secret",
        namespace="tenant",
        name="private",
        propagation_policy="Foreground",
        uid="original-uid",
        resource_version="42",
    )


def test_replacement_delete_is_a_conflict_and_never_retried_unconditionally(driver):
    instance, client = driver
    client.delete.side_effect = PreconditionFailedError("replacement uid")
    result = instance.delete_manifests("cluster", "tenant", [manifest(uid="original-uid")])
    assert not result.ok and result.conflicts and not result.errors and not result.deleted
    assert client.delete.call_count == 1
    assert client.delete.call_args.kwargs["uid"] == "original-uid"


def test_unconditional_delete_compatibility_is_preserved(driver):
    instance, client = driver
    assert instance.delete_manifests("cluster", "tenant", [manifest()]).ok
    assert client.delete.call_args.kwargs.get("uid") is None
    assert client.delete.call_args.kwargs.get("resource_version") is None


class Conflict(Exception):
    pass


@pytest.fixture
def dynamic(monkeypatch):
    import sys
    from types import SimpleNamespace

    resource = Mock(namespaced=True)
    client = object.__new__(KubernetesDynamicClient)
    monkeypatch.setattr(client, "_refresh_token", Mock())
    monkeypatch.setattr(client, "_resource_for", Mock(return_value=resource))
    monkeypatch.setitem(sys.modules, "kubernetes.dynamic.exceptions", SimpleNamespace(ConflictError=Conflict))
    return client, resource


@pytest.mark.parametrize("namespaced", [True, False])
@pytest.mark.parametrize("dry_run", [True, False])
def test_dynamic_create_uses_post_and_supports_scope_and_dry_run(dynamic, namespaced, dry_run):
    client, resource = dynamic
    resource.namespaced = namespaced
    body = manifest()
    assert client.create_manifest(namespace="tenant", manifest=body, dry_run=dry_run) == "created"
    expected = {"body": body, "namespace": "tenant" if namespaced else None}
    if dry_run:
        expected["dry_run"] = "All"
    resource.create.assert_called_once_with(**expected)
    resource.get.assert_not_called()
    resource.server_side_apply.assert_not_called()
    client._refresh_token.assert_called_once()


def test_atomic_create_refuses_a_competing_owner_without_read_apply_fallback(dynamic):
    client, resource = dynamic
    resource.create.side_effect = Conflict("the Kubernetes server refused an occupied name")
    with pytest.raises(AlreadyExistsError, match="refused adoption"):
        client.create_manifest(namespace="tenant", manifest=manifest())
    assert resource.create.call_count == 1
    resource.get.assert_not_called()
    resource.server_side_apply.assert_not_called()


@pytest.mark.parametrize("body", [{}, {"kind": "Secret", "metadata": {"generateName": "private-"}}])
def test_create_only_requires_an_exact_resource_name(dynamic, body):
    client, resource = dynamic
    with pytest.raises(ValueError, match=r"kind and metadata\.name"):
        client.create_manifest(namespace="tenant", manifest=body)
    resource.create.assert_not_called()
