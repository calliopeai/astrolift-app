from __future__ import annotations

import copy
from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_pull_authority as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, INTENT, JOB
from tests.nvke.test_operator_install_plan_immutable import SOURCE, client
from tests.nvke.test_operator_install_plan_immutable import body as immutable_body

ARTIFACT = str(uuid4())


@pytest.mark.parametrize(
    "change",
    [
        {"maximumBytes": 2**53 + 1},
        {"maximumBytes": "67108865"},
        {"maximumBytes": "01"},
        {"maximumCalls": True},
        {"maximumCalls": 3},
        {"timeoutSeconds": 301},
        {"selectedPods": {}},
        {"selectedPods": {"owned/pod": ""}},
        {"selectedPods": {"../pod": "uid"}},
        {"selectedPods": {"owned/pod": "same", "owned/other": "same"}},
        {"selectedPods": {"n" * 64 + "/pod": "uid"}},
        {"credentialPath": "/PRIVATE"},
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
        {"podCount": 2},
        {"podCount": True},
        {"serviceAccountCount": 2},
        {"secretCount": 256},
        {"missingReferenceCount": 3},
        {"continuationAuthorized": True},
        {"executionState": "ready"},
        {"observedAt": "2020-01-01T00:00:00+00:00"},
        {"credentials": "PRIVATE"},
        {"selectedPods": {"owned/pod": "uid"}},
        {"authorityDigest": "invalid"},
    ],
)
def test_response_is_bound_and_contains_only_public_metadata(change):
    sdk, _ = peer(response={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


def body():
    return {
        **{k: v for k, v in immutable_body(tree=True).items() if k in protocol.BASE},
        "artifactId": ARTIFACT,
        "selectedPods": {"owned/pod": "pod-uid"},
        "maximumBytes": "1048576",
        "maximumCalls": 512,
        "timeoutSeconds": 300,
    }


def summary():
    return {
        "format": 1,
        "artifactId": ARTIFACT,
        **{k: "a" * 64 for k in ("artifactDigest", "sourceDigest", "captureDigest", "authorityDigest")},
        "observedAt": DATE,
        "podCount": 1,
        "serviceAccountCount": 1,
        "secretCount": 1,
        "missingReferenceCount": 0,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(*, response=None, lost=False, capability=None):
    sdk, calls = client()
    original = sdk._http._transport
    response = summary() if response is None else response

    def respond(request):
        if request.url.path.endswith("/install-plan-pull-authority"):
            calls.append((request.method, request.url.path, dict(request.url.params)))
            if lost and request.method == "POST":
                raise httpx.ReadTimeout("PRIVATE", request=request)
            return httpx.Response(200, json=copy.deepcopy(response))
        result = original.handle_request(request)
        if request.url.path == "/v1/identity":
            data = result.json()
            data["installPlanPullAuthority"] = (
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
    method = sdk.install_plan_pull_authority if read_only else sdk.prepare_install_plan_pull_authority
    return method(JOB, intent=INTENT, body=value, **SOURCE)


def test_prepare_and_offline_read_preserve_closed_summary():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1
    query = next(q for method, path, q in calls if method == "GET" and path.endswith("/install-plan-pull-authority"))
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


def test_read_remains_available_when_preparation_is_disabled():
    sdk, calls = peer(capability={"format": 1, "prepare": False, "readOnly": True, "continuation": False})
    with sdk:
        assert call(sdk, read_only=True) == summary()
        with pytest.raises(NvkeError) as error:
            call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(method == "GET" for method, _, _ in calls)


def test_changed_binding_refuses_before_pull_observation():
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), "bindingDigest": "e" * 64})
    assert error.value.code == "SOURCE_CHANGED"
    assert all(method == "GET" for method, _, _ in calls)
    assert not any(path.endswith("/install-plan-pull-authority") for _, path, _ in calls)


def test_binding_change_after_observation_refuses_result():
    sdk, calls = peer()
    transport = sdk._http._transport
    observed = False

    def respond(request):
        nonlocal observed
        response = transport.handle_request(request)
        if request.url.path.endswith("/install-plan-pull-authority"):
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
