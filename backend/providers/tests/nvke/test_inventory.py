from __future__ import annotations

import httpx
import pytest

from nvke.client import Endpoint, NvkeClient, NvkeError
from nvke.datastore import NvkeDatastore
from nvke.inventory import collect, collection, collections, resource_actions


@pytest.mark.parametrize("budget", ["MAX_INVENTORY_ITEMS", "MAX_INVENTORY_BYTES", "MAX_INVENTORY_PAGES"])
def test_inventory_budget_exhaustion_fails_instead_of_returning_a_partial_collection(monkeypatch, budget):
    from nvke import inventory

    monkeypatch.setattr(inventory, budget, 1)
    calls = []

    def respond(_request):
        calls.append(True)
        return httpx.Response(
            200,
            json={
                "items": [{"metadata": {"name": f"resource-{len(calls)}"}}],
                "metadata": {"resourceVersion": "stable", "continue": f"page-{len(calls)}"},
            },
        )

    with (
        NvkeDatastore(
            Endpoint("datastore", "https://native.example.test"),
            organization_namespace="org-owned",
            transport=httpx.MockTransport(respond),
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        collect(client, "datastore.listMachineImage", {}, "org-owned")
    assert error.value.code == "INVENTORY_LIMIT"


def test_every_native_resource_list_has_a_collection_and_its_original_actions():
    inventory = collections()
    assert len(inventory) == len({item.key for item in inventory}) == 18
    assert {item.surface for item in inventory} == {"controller", "tenant", "operator", "datastore"}
    for item in inventory:
        assert item.key in {selected.key for selected in resource_actions(item)}
        assert not any(selected.surface != item.surface for selected in resource_actions(item))
    assert {selected.key for selected in resource_actions(collection("listClusters"))} == {
        "listClusters",
        "createCluster",
        "getCluster",
        "updateCluster",
        "deleteCluster",
        "getClusterControlPlane",
        "getClusterControlPlaneConfigHistory",
        "getClusterSpireServer",
        "updateClusterSpireServer",
    }
    assert collection("operator.listMachineImage").permission == "nvke.admin"


@pytest.mark.parametrize("surface", ["datastore", "tenant", "operator"])
def test_collect_follows_all_native_pages_without_losing_filters_or_integer_values(surface):
    requests = []
    checkpoints = []

    def respond(request):
        requests.append(request)
        index = len(requests)
        item = {
            "metadata": {"name": f"resource-{index}", "namespace": "org-owned"},
            "spec": {"nativeInteger": 9007199254740993},
        }
        data = {"items": [item]}
        if surface == "datastore":
            data["metadata"] = {"resourceVersion": "frozen-rv", "continue": "next+/=" if index == 1 else ""}
        else:
            data.update(pageKey="next+/=" if index == 1 else "", hasMore=index == 1)
        return httpx.Response(200, json=data)

    options = {"transport": httpx.MockTransport(respond), "before_request": lambda: checkpoints.append(True)}
    endpoint = Endpoint(surface, "https://native.example.test")
    client = (
        NvkeDatastore(endpoint, organization_namespace="org-owned", **options)
        if surface == "datastore"
        else NvkeClient([endpoint], **options)
    )
    key = (
        "datastore.listNVKECluster"
        if surface == "datastore"
        else f"{surface}.listNVKECluster"
        if surface == "tenant"
        else "operator.listMachineImage"
    )
    parameters = (
        {"page_size": 1, "label_selector": "group=owned"} if surface == "datastore" else {"query": {"pageSize": 1}}
    )
    with client:
        result = collect(client, key, parameters, "org-owned")
    assert len(requests) == len(checkpoints) == result.data["native_page_count"] == 2
    assert result.data["complete"] is True
    assert result.data["items"][1]["spec"]["nativeInteger"] == 9007199254740993
    assert requests[1].url.params["continue" if surface == "datastore" else "pageKey"] == "next+/="
    if surface == "datastore":
        assert requests[1].url.params["labelSelector"] == "group=owned"
        assert result.resource_version == "frozen-rv"
    assert "continue_token" not in parameters


@pytest.mark.parametrize("failure", ["cycle", "version", "duplicate", "namespace", "shape"])
def test_invalid_or_inconsistent_pages_never_become_complete_inventory(failure):
    calls = []

    def respond(_request):
        calls.append(True)
        name = "duplicate" if failure == "duplicate" else f"resource-{len(calls)}"
        data = {
            "items": [{"metadata": {"name": name, "namespace": "foreign" if failure == "namespace" else "org-owned"}}],
            "metadata": {
                "resourceVersion": str(len(calls)) if failure == "version" else "stable",
                "continue": "repeat" if len(calls) == 1 or failure == "cycle" else "",
            },
        }
        if failure == "shape":
            data["items"] = "PRIVATE_NATIVE_PAYLOAD"
        return httpx.Response(200, json=data)

    with (
        NvkeDatastore(
            Endpoint("datastore", "https://native.example.test"),
            organization_namespace="org-owned",
            transport=httpx.MockTransport(respond),
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        collect(client, "datastore.listNVKECluster", {}, "org-owned")
    assert error.value.code in {"INVALID_RESPONSE", "SOURCE_CHANGED"}
    assert "PRIVATE_NATIVE_PAYLOAD" not in str(error.value)


def test_authority_is_checked_again_before_every_native_page():
    calls = []

    def checkpoint():
        if calls:
            raise NvkeError("AUTHORITY_REVOKED", "Authority was revoked")

    def respond(_request):
        calls.append(True)
        return httpx.Response(200, json={"items": [], "metadata": {"resourceVersion": "frozen", "continue": "next"}})

    with (
        NvkeDatastore(
            Endpoint("datastore", "https://native.example.test"),
            organization_namespace="org-owned",
            transport=httpx.MockTransport(respond),
            before_request=checkpoint,
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        collect(client, "datastore.listNVKECluster", {}, "org-owned")
    assert error.value.code == "AUTHORITY_REVOKED"
    assert len(calls) == 1


def test_an_inventory_read_cannot_start_in_the_middle_or_execute_a_write():
    def forbidden(_request):
        raise AssertionError("invalid inventory performed network I/O")

    with NvkeDatastore(
        Endpoint("datastore", "https://native.example.test"),
        organization_namespace="org-owned",
        transport=httpx.MockTransport(forbidden),
    ) as client:
        for key, parameters in (
            ("datastore.listNVKECluster", {"continue_token": "middle"}),
            ("datastore.deleteNVKECluster", {"name": "private"}),
        ):
            with pytest.raises(NvkeError):
                collect(client, key, parameters, "org-owned")
