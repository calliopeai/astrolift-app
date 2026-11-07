from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from nvke.client import Endpoint, NvkeClient, NvkeError
from nvke.contract import Operation, controller_spec, operations
from tests.nvke.support import PUBLIC_SPECS, TOKEN, UUID, Peer


def arguments(op: Operation) -> dict[str, Any]:
    path = {}
    if "{name}" in op.path:
        path["name"] = "resource-a"
    for name in ("clusterID", "nodePoolID", "nodeSetID", "nodeID"):
        if "{" + name + "}" in op.path:
            path[name] = UUID
    result: dict[str, Any] = {"path": path}
    if op.surface == "controller" and op.method in {"POST", "PATCH"}:
        declaration = controller_spec()["paths"][op.path][op.method.lower()]
        content = declaration["requestBody"]["content"]["application/json"]
        result["body"] = copy.deepcopy(content.get("example", {}))
        if op.id == "updateClusterSpireServer":
            result["body"] = {"address": "spire.example.com", "port": 30440}
    if op.surface != "controller":
        if op.method in {"POST", "PUT"}:
            result["body"] = {"metadata": {"name": "resource-a"}, "spec": PUBLIC_SPECS[op.resource]}
        if op.method in {"PUT", "DELETE"}:
            result["resource_version"] = "opaque-rv-17"
    return result


@pytest.mark.parametrize("op", operations(), ids=lambda op: op.id)
def test_every_pinned_operation_reaches_its_own_surface_over_real_http(peer: Peer, op: Operation) -> None:
    endpoints = [
        Endpoint(surface=s, url=f"{peer.url}/{s}", token=lambda: TOKEN, allow_http=True)
        for s in ("controller", "tenant", "operator")
    ]
    kwargs = arguments(op)
    with NvkeClient(endpoints) as client:
        result = client.execute(op.id, **kwargs)
    request = peer.requests.pop()
    expected = op.path
    for name, value in kwargs["path"].items():
        expected = expected.replace("{" + name + "}", value)
    assert request["path"] == f"/{op.surface}{expected}"
    assert request["method"] == op.method
    assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert result.data["futureField"] == {"kept": True}
    assert result.resource_version == "opaque-rv-17"
    assert len(peer.requests) == 0
    if "body" in kwargs:
        assert json.loads(request["body"]) == kwargs["body"]
    else:
        assert request["body"] is None
    if "resource_version" in kwargs:
        assert request["headers"]["If-Match"] == '"opaque-rv-17"'


@pytest.mark.parametrize(
    "url",
    [
        "http://site.example.com",
        "https://user:secret@site.example.com",
        "https://site.example.com/?secret=x",
        "https://site.example.com/#fragment",
        "https://site.example.com/a/../b",
        "https://site.example.com/%2e%2e",
        "https://site.example.com\\other",
        "https://site.example.com:0",
        "https://site.example.com:99999",
        "https://site.example.com\n",
        "ftp://site.example.com",
        "https://",
    ],
)
def test_invalid_endpoint_never_echoes_credentials(url: str) -> None:
    with pytest.raises(ValueError) as exc:
        Endpoint("controller", url)
    assert url not in str(exc.value)
    assert "secret" not in str(exc.value)


def test_surface_is_not_silently_substituted(peer: Peer) -> None:
    with NvkeClient([Endpoint("controller", peer.url, allow_http=True)]) as client, pytest.raises(NvkeError) as exc:
        client.execute("operator.listMachineImage")
    assert exc.value.code == "SURFACE_UNAVAILABLE"
    assert not peer.requests


@pytest.mark.parametrize("path", ["..", "a/b", "a%2fb", "a?secret=x", "a#token", "a\\b", "a\x00", ""])
def test_path_injection_is_rejected_before_network(peer: Peer, path: str) -> None:
    with (
        NvkeClient([Endpoint("operator", peer.url, allow_http=True)]) as client,
        pytest.raises(NvkeError, match="invalid"),
    ):
        client.execute("operator.getMachineImage", path={"name": path})
    assert not peer.requests


@pytest.mark.parametrize(
    "operation_id,kwargs",
    [
        ("getCluster", {"path": {"clusterID": "not-a-uuid"}}),
        ("getCluster", {"path": {"clusterID": UUID, "extra": "x"}}),
        ("listClusters", {"query": {"token": "private-secret"}}),
        ("listClusters", {"body": {"secret": "private-secret"}}),
        ("createCluster", {"body": {"secret": "private-secret"}}),
        ("updateCluster", {"path": {"clusterID": UUID}, "body": {"secret": "private-secret"}}),
        ("operator.listMachineImage", {"query": {"pageSize": 501}}),
        ("operator.listMachineImage", {"query": {"pageSize": True}}),
        ("operator.listMachineImage", {"query": {"pageSize": "2"}}),
        ("operator.listMachineImage", {"query": {"extra": "private-secret"}}),
        ("operator.createMachineImage", {"body": {"metadata": {"name": "a", "namespace": "foreign"}, "spec": {}}}),
        ("operator.deleteMachineImage", {"path": {"name": "a"}, "resource_version": "*"}),
        ("operator.deleteMachineImage", {"path": {"name": "a"}}),
        (
            "operator.updateMachineImage",
            {
                "path": {"name": "other"},
                "body": {"metadata": {"name": "a"}, "spec": PUBLIC_SPECS["MachineImage"]},
                "resource_version": "17",
            },
        ),
    ],
)
def test_invalid_inputs_are_redacted_and_never_sent(peer: Peer, operation_id: str, kwargs: dict[str, Any]) -> None:
    with (
        NvkeClient([Endpoint(s, peer.url, allow_http=True) for s in ("controller", "tenant", "operator")]) as client,
        pytest.raises(NvkeError) as exc,
    ):
        client.execute(operation_id, **kwargs)
    assert exc.value.code == "INVALID_INPUT"
    assert "private-secret" not in str(exc.value)
    assert not peer.requests


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (400, "INVALID_INPUT", False),
        (401, "UNAUTHENTICATED", False),
        (403, "PERMISSION_DENIED", False),
        (404, "NOT_FOUND", False),
        (409, "CONFLICT", False),
        (412, "CONFLICT", False),
        (429, "RATE_LIMITED", True),
        (500, "UPSTREAM_ERROR", True),
        (503, "UPSTREAM_ERROR", True),
    ],
)
def test_http_failure_classification_and_payload_redaction(peer: Peer, status: int, code: str, retryable: bool) -> None:
    peer.status = status
    peer.body = f'{{"message":"{TOKEN}"}}'.encode()
    with NvkeClient([Endpoint("controller", peer.url, allow_http=True)]) as client, pytest.raises(NvkeError) as exc:
        client.execute("listClusters")
    assert exc.value.code == code
    assert exc.value.retryable is retryable
    assert not exc.value.ambiguous
    assert TOKEN not in str(exc.value)
    assert len(peer.requests) == 1


def test_redirect_is_not_followed_with_credentials(peer: Peer) -> None:
    peer.status = 307
    peer.headers["Location"] = peer.url + "/stolen"
    with (
        NvkeClient([Endpoint("controller", peer.url, token=lambda: TOKEN, allow_http=True)]) as client,
        pytest.raises(NvkeError),
    ):
        client.execute("listClusters")
    assert len(peer.requests) == 1


def test_lost_mutation_response_is_ambiguous_and_not_retried(peer: Peer) -> None:
    peer.disconnect = True
    with NvkeClient([Endpoint("controller", peer.url, allow_http=True)]) as client, pytest.raises(NvkeError) as exc:
        client.execute("deleteCluster", path={"clusterID": UUID})
    assert exc.value.code == "TRANSPORT_ERROR"
    assert exc.value.ambiguous
    assert exc.value.retryable
    assert len(peer.requests) == 1


def test_token_is_reloaded_for_every_request_and_hidden_from_repr(peer: Peer) -> None:
    tokens = iter(["token-one", "token-two"])
    endpoint = Endpoint("controller", peer.url, token=lambda: next(tokens), allow_http=True)
    assert "token" not in repr(endpoint)
    assert peer.url not in repr(endpoint)
    with NvkeClient([endpoint]) as client:
        client.execute("listClusters")
        client.execute("listClusters")
    assert [r["headers"]["Authorization"] for r in peer.requests] == ["Bearer token-one", "Bearer token-two"]


def test_broken_credential_loader_does_not_expose_exception(peer: Peer) -> None:
    def broken() -> str:
        raise ValueError(TOKEN)

    with (
        NvkeClient([Endpoint("controller", peer.url, token=broken, allow_http=True)]) as client,
        pytest.raises(NvkeError) as exc,
    ):
        client.execute("listClusters")
    assert exc.value.code == "CREDENTIALS_UNAVAILABLE"
    assert TOKEN not in str(exc.value)
    assert not peer.requests


@pytest.mark.parametrize("token", ["", "a\r\nX-Stolen: b", "a b", "a\x7f", "privaté"])
def test_invalid_bearer_never_reaches_network(peer: Peer, token: str) -> None:
    with (
        NvkeClient([Endpoint("controller", peer.url, token=lambda: token, allow_http=True)]) as client,
        pytest.raises(NvkeError) as exc,
    ):
        client.execute("listClusters")
    assert exc.value.code == "CREDENTIALS_UNAVAILABLE"
    assert not peer.requests


def test_response_size_limit(peer: Peer) -> None:
    with (
        NvkeClient([Endpoint("controller", peer.url, allow_http=True)], max_response_bytes=10) as client,
        pytest.raises(NvkeError) as exc,
    ):
        client.execute("listClusters")
    assert exc.value.code == "RESPONSE_TOO_LARGE"


@pytest.mark.parametrize("status,body", [(204, b""), (200, b"")])
def test_empty_delete_and_health_responses(peer: Peer, status: int, body: bytes) -> None:
    peer.status = status
    peer.body = body
    op = "deleteCluster" if status == 204 else "operator.healthz"
    kwargs = {"path": {"clusterID": UUID}} if status == 204 else {}
    with NvkeClient([Endpoint(s, peer.url, allow_http=True) for s in ("controller", "operator")]) as client:
        assert client.execute(op, **kwargs).data is None


def test_invalid_success_payload_is_not_echoed(peer: Peer) -> None:
    peer.body = TOKEN.encode()
    with NvkeClient([Endpoint("controller", peer.url, allow_http=True)]) as client, pytest.raises(NvkeError) as exc:
        client.execute("deleteCluster", path={"clusterID": UUID})
    assert exc.value.code == "INVALID_RESPONSE"
    assert exc.value.ambiguous
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize("peer", [True], indirect=True)
def test_https_verifies_the_configured_site_ca(peer: Peer) -> None:
    with NvkeClient([Endpoint("controller", peer.url, token=lambda: TOKEN)], verify=peer.verify) as client:
        assert client.execute("listClusters").status_code == 200
    assert len(peer.requests) == 1


@pytest.mark.parametrize("peer", [True], indirect=True)
def test_untrusted_tls_certificate_is_rejected_without_sending_credential(peer: Peer) -> None:
    with (
        NvkeClient([Endpoint("controller", peer.url, token=lambda: TOKEN)]) as client,
        pytest.raises(NvkeError) as exc,
    ):
        client.execute("listClusters")
    assert exc.value.code == "TRANSPORT_ERROR"
    assert not peer.requests


def test_ambient_proxy_environment_does_not_receive_site_credentials(
    peer: Peer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with NvkeClient([Endpoint("controller", peer.url, token=lambda: TOKEN, allow_http=True)]) as client:
        assert client.execute("listClusters").status_code == 200


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {"not-json"}])
def test_non_json_values_are_rejected_without_an_http_call(peer: Peer, value: Any) -> None:
    spec = controller_spec()["paths"]["/clusters"]["post"]["requestBody"]["content"]["application/json"]["example"]
    spec["extra"] = value
    with (
        NvkeClient([Endpoint("controller", peer.url, allow_http=True)]) as client,
        pytest.raises(NvkeError) as exc,
    ):
        client.execute("createCluster", body=spec)
    assert exc.value.code == "INVALID_INPUT"
    assert not peer.requests


@pytest.mark.parametrize("raw", [b'{"value":NaN}', b'{"value":Infinity}'])
def test_non_json_numbers_in_success_response_are_refused(peer: Peer, raw: bytes) -> None:
    peer.body = raw
    with (
        NvkeClient([Endpoint("controller", peer.url, allow_http=True)]) as client,
        pytest.raises(NvkeError) as exc,
    ):
        client.execute("listClusters")
    assert exc.value.code == "INVALID_RESPONSE"


def test_public_pagination_and_delete_policy_use_the_upstream_query_names(peer: Peer) -> None:
    with NvkeClient([Endpoint("operator", peer.url, allow_http=True)]) as client:
        client.execute("operator.listMachineImage", query={"pageSize": 2, "pageKey": "opaque+/="})
        client.execute(
            "operator.deleteMachineImage",
            path={"name": "image-a"},
            query={"propagationPolicy": "foreground"},
            resource_version="rv-1",
        )
    assert peer.requests[0]["path"] == "/v1/machine-images?pageSize=2&pageKey=opaque%2B%2F%3D"
    assert peer.requests[1]["path"] == "/v1/machine-images/image-a?propagationPolicy=foreground"
