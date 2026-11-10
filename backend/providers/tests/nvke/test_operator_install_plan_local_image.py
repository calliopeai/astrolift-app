from __future__ import annotations

import json

import httpx
import pytest

from nvke import _operator_install_plan_image as protocol
from nvke.client import NvkeError
from tests.nvke import test_operator_install_plan_image as fixture

CAPABILITY = {"format": 1, "prepare": True, "readOnly": True, "continuation": False}


def body():
    value = {key: item for key, item in fixture.body().items() if key not in {"policy", "maximumCalls"}}
    return {**value, "sourceKind": "local", "reference": "example.test/local@sha256:" + "4" * 64}


def summary():
    return {**fixture.summary(), "sourceKind": "local"}


def peer(*, capability=CAPABILITY, registry=False, image=None, **options):
    sdk, calls = fixture.peer(image=summary() if image is None else image, **options)
    original = sdk._http._transport
    posted = []

    def respond(request):
        if request.method == "POST" and request.url.path.endswith("/install-plan-image"):
            posted.append(json.loads(request.content))
        response = original.handle_request(request)
        if request.url.path == "/v1/identity":
            data = response.json()
            if not registry:
                data.pop("installPlanImage", None)
            if capability is not None:
                data["installPlanLocalImage"] = capability
            return httpx.Response(response.status_code, json=data)
        return response

    sdk._http._transport = httpx.MockTransport(respond)
    return sdk, calls, posted


def call(sdk, *, read_only=False, value=None):
    value = body() if value is None else value
    if read_only:
        value = {key: item for key, item in value.items() if key in protocol.LOCAL_BASE}
    method = sdk.install_plan_image if read_only else sdk.prepare_install_plan_image
    return method(
        fixture.JOB,
        intent=fixture.INTENT,
        body=value,
        chart_artifact_id=fixture.ARTIFACT,
        chart_artifact_digest="c" * 64,
    )


def test_local_prepare_and_cold_read_need_only_local_capability_and_exact_kind():
    sdk, calls, posted = peer()
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    assert posted == [body()]
    reads = [query for method, path, query in calls if method == "GET" and path.endswith("/install-plan-image")]
    assert reads == [{key: str(item) for key, item in body().items() if key in protocol.LOCAL_BASE - {"format"}}]
    assert "policy" not in reads[0] and "maximumCalls" not in reads[0]


@pytest.mark.parametrize(
    "change",
    [
        {"sourceKind": "registry"},
        {"sourceKind": None},
        {"sourceKind": "LOCAL"},
        {"policy": "reviewed"},
        {"maximumCalls": 1},
        {"reference": "example.test/local:v1"},
        {"reference": "example.test/local@sha256:short"},
        {"reference": body()["reference"] + "\n"},
        {"maximumBytes": True},
        {"maximumBytes": "0"},
        {"timeoutSeconds": True},
        {"credentials": "PRIVATE"},
    ],
)
def test_invalid_local_requests_stop_before_any_peer_io(change):
    sdk, calls, posted = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT"
    assert calls == [] and posted == []


@pytest.mark.parametrize(
    "change",
    [
        {"sourceKind": "registry"},
        {"sourceKind": None},
        {"sourceKind": "local\n"},
        {"rootDigest": "sha256:" + "5" * 64},
        {"continuationAuthorized": True},
        {"reference": "PRIVATE"},
        {"policy": "PRIVATE"},
        {"bytes": "1073741825"},
    ],
)
def test_local_metadata_cannot_change_kind_digest_or_authorization(change):
    sdk, _, _ = peer(image={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


def test_registry_capability_does_not_authorize_local_capture():
    sdk, calls, posted = peer(capability=None, registry=True)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert posted == [] and all(method == "GET" for method, _, _ in calls)


def test_local_reply_without_the_kind_discriminator_is_refused():
    sdk, _, _ = peer(image=fixture.summary())
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


def test_lost_local_reply_never_replays_and_explicit_read_preserves_the_kind():
    sdk, calls, posted = peer(lost_image_reply=True)
    with sdk:
        with pytest.raises(NvkeError):
            call(sdk)
        assert call(sdk, read_only=True) == summary()
    assert posted == [body()]
    assert len([1 for method, path, _ in calls if method == "POST" and path.endswith("/install-plan-image")]) == 1
