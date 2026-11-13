from __future__ import annotations

import copy
from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_node_platforms as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, INTENT, JOB
from tests.nvke.test_operator_install_plan_immutable import SOURCE, client
from tests.nvke.test_operator_install_plan_immutable import body as immutable_body

ARTIFACT = str(uuid4())


def body():
    return {
        **{k: v for k, v in immutable_body(tree=True).items() if k in protocol.BASE},
        "artifactId": ARTIFACT,
        "nodePlatforms": {
            "owned-node": {
                "uid": "owned-uid",
                "runtimeProfile": "containerd/v2.3.0",
                "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"},
            }
        },
        **{k: str(min(v, 2**53 + 1)) for k, v in protocol.BYTE_LIMITS.items()},
        "maximumCalls": 512,
        "maximumPairs": 10000,
        "timeoutSeconds": 300,
    }


def summary():
    return {
        "format": 1,
        "artifactId": ARTIFACT,
        **{k: "a" * 64 for k in ("artifactDigest", "sourceDigest", "captureDigest", "platformDigest")},
        "observedAt": DATE,
        "nodeCount": 1,
        "contentCount": 1,
        "imageReferenceCount": 1,
        "pairCount": 1,
        "issueCount": 3,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(*, response=None, lost=False, capability=None):
    sdk, calls = client()
    original = sdk._http._transport
    response = summary() if response is None else response

    def respond(request):
        if request.url.path.endswith("/install-plan-node-platforms"):
            calls.append((request.method, request.url.path, dict(request.url.params)))
            if lost and request.method == "POST":
                raise httpx.ReadTimeout("PRIVATE", request=request)
            return httpx.Response(200, json=copy.deepcopy(response))
        result = original.handle_request(request)
        if request.url.path == "/v1/identity":
            data = result.json()
            data["installPlanNodePlatforms"] = (
                capability
                if capability is not None
                else {
                    "format": 1,
                    "prepare": True,
                    "readOnly": True,
                    "continuation": False,
                }
            )
            return httpx.Response(200, json=data)
        return result

    sdk._http._transport = httpx.MockTransport(respond)
    return sdk, calls


def call(sdk, *, read_only=False, value=None):
    value = body() if value is None else value
    if read_only:
        value = {k: v for k, v in value.items() if k in protocol.BASE}
    method = sdk.install_plan_node_platforms if read_only else sdk.prepare_install_plan_node_platforms
    return method(JOB, intent=INTENT, body=value, **SOURCE)


@pytest.mark.parametrize("profile", sorted(protocol.PROFILES))
def test_each_exact_runtime_profile_reaches_the_sdk_transport(profile):
    sdk, calls = peer()
    value = body()
    value["nodePlatforms"]["owned-node"]["runtimeProfile"] = profile
    with sdk:
        assert call(sdk, value=value) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1


@pytest.mark.parametrize("profile", ["containerd/v2.3.3", "containerd/v2.3.5", "containerd/v2.3.4+vendor"])
def test_unqualified_neighboring_versions_refuse_before_transport(profile):
    sdk, calls = peer()
    value = body()
    value["nodePlatforms"]["owned-node"]["runtimeProfile"] = profile
    with sdk, pytest.raises(NvkeError):
        call(sdk, value=value)
    assert calls == []


def test_prepare_and_offline_read_preserve_closed_summary():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1
    query = next(q for method, path, q in calls if method == "GET" and path.endswith("/install-plan-node-platforms"))
    assert set(query) == protocol.BASE - {"format"}


def test_lost_reply_has_no_automatic_preparation_retry():
    sdk, calls = peer(lost=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            call(sdk)
        assert error.value.ambiguous and not error.value.retryable
        assert "PRIVATE" not in str(error.value)
        assert call(sdk, read_only=True) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"maximumImageBytes": 2**53 + 1},
        {"maximumHelperBytes": "01"},
        {"maximumCalls": True},
        {"maximumCalls": 7},
        {"maximumPairs": 0},
        {"timeoutSeconds": 301},
        {"nodePlatforms": {}},
        {"helperProgram": "/PRIVATE"},
        {"artifactId": SOURCE["workload_id"]},
        {"continuationAuthorized": True},
    ],
)
def test_invalid_requests_refuse_before_transport(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT" and not calls


@pytest.mark.parametrize(
    "change",
    [
        {"nodeCount": 2},
        {"contentCount": 2},
        {"imageReferenceCount": 2},
        {"pairCount": 2},
        {"issueCount": True},
        {"continuationAuthorized": True},
        {"executionState": "ready"},
        {"observedAt": "2020-01-01T00:00:00+00:00"},
        {"credentials": "PRIVATE"},
    ],
)
def test_response_is_bound_to_selected_nodes_and_signed_image_source(change):
    sdk, _ = peer(response={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


def test_read_remains_available_when_preparation_is_disabled():
    sdk, calls = peer(capability={"format": 1, "prepare": False, "readOnly": True, "continuation": False})
    with sdk:
        assert call(sdk, read_only=True) == summary()
        with pytest.raises(NvkeError) as error:
            call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(method == "GET" for method, _, _ in calls)


def test_changed_binding_refuses_before_node_observation():
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), "bindingDigest": "e" * 64})
    assert error.value.code == "SOURCE_CHANGED"
    assert all(method == "GET" for method, _, _ in calls)
    assert not any(path.endswith("/install-plan-node-platforms") for _, path, _ in calls)


def test_binding_change_after_observation_refuses_result():
    sdk, calls = peer()
    transport = sdk._http._transport
    observed = False

    def respond(request):
        nonlocal observed
        response = transport.handle_request(request)
        if request.url.path.endswith("/install-plan-node-platforms"):
            observed = True
        elif observed and request.url.path.endswith("/install-plan-image-binding"):
            data = response.json()
            data["bindingDigest"] = "e" * 64
            return httpx.Response(200, json=data)
        return response

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "SOURCE_CHANGED"
    assert sum(method == "POST" for method, _, _ in calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"uid": "../private"},
        {"runtimeProfile": "containerd/v99"},
        {"platform": {"os": "linux", "architecture": "arm64", "helperProgram": "/PRIVATE"}},
        {"platform": {"os": "linux", "architecture": "arm64", "os.features": "bad"}},
    ],
)
def test_node_scope_is_closed_before_transport(change):
    value = body()
    value["nodePlatforms"]["owned-node"].update(change)
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value=value)
    assert error.value.code == "INVALID_INPUT" and not calls
