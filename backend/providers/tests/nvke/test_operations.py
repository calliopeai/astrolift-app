from __future__ import annotations

import copy

import pytest

from nvke.client import Endpoint, NvkeClient, NvkeError
from nvke.contract import operations
from nvke.operations import actions, validate_request
from tests.nvke.support import PUBLIC_SPECS
from tests.nvke.test_client import arguments


@pytest.mark.parametrize("selected", actions(), ids=lambda selected: selected.key)
def test_every_catalog_action_has_a_valid_complete_request_without_contacting_a_site(selected):
    if selected.http:
        request = arguments(selected.http)
    else:
        request = {}
        if selected.verb in {"get", "replace", "delete", "deprecate"}:
            request["name"] = "resource-a"
        if selected.verb in {"replace", "delete", "deprecate"}:
            request["resource_version"] = "opaque-rv-17"
        if selected.verb in {"delete", "deprecate"}:
            request["uid"] = "uid-17"
        if selected.verb in {"create", "replace"}:
            request["document"] = {
                "metadata": {"name": "resource-a"},
                "spec": copy.deepcopy(PUBLIC_SPECS[selected.kind]),
            }
        if selected.verb == "deprecate":
            request["deprecated"] = True
        if selected.verb == "kubeconfig":
            request = {"cluster_name": "cluster-a", "management_namespace": "nvke-system"}
    assert validate_request(selected.key, request, "org-owned") == selected


def test_full_http_surface_is_retained_and_unknown_dispatch_cannot_be_proxied():
    assert {item.id for item in operations()} <= {item.key for item in actions()}
    assert len(actions()) == len({item.key for item in actions()}) == 88
    with pytest.raises(NvkeError, match="Unknown NVKE operation"):
        validate_request("datastore.deleteSecret", {"name": "secret"}, "org-owned")


def test_operator_and_credential_actions_require_admin_authority():
    for selected in actions():
        if selected.surface == "operator" or selected.verb == "kubeconfig":
            assert selected.permission == "nvke.admin"
    assert validate_request("listClusters", {}, "org-owned").permission == "nvke.read"


def test_prepare_never_loads_credentials_or_runs_a_checkpoint():
    def forbidden():
        raise AssertionError("preparation must perform no external action")

    with NvkeClient(
        [Endpoint("controller", "https://unused.invalid", token=forbidden)], before_request=forbidden
    ) as client:
        assert client.prepare("listClusters").operation.id == "listClusters"


def test_declared_empty_202_is_an_accepted_spire_update(peer):
    peer.status = 202
    peer.body = b""
    with NvkeClient([Endpoint("controller", peer.url, allow_http=True)]) as client:
        value = client.execute(
            "updateClusterSpireServer",
            path={"clusterID": "123e4567-e89b-12d3-a456-426614174000"},
            body={"address": "spire.example.test", "port": 30440},
        )
    assert value.status_code == 202
    assert value.data is None
