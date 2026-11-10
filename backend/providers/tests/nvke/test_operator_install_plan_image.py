from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_image as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, DIGEST, INTENT, JOB, PLAN, client
from tests.nvke.test_operator_install_plan_charts import ARTIFACT
from tests.nvke.test_operator_install_plan_charts import summary as chart_summary
from tests.nvke.test_operator_install_plan_tree import TREE
from tests.nvke.test_operator_install_plan_tree import summary as tree_summary

IMAGE = str(uuid4())


def body():
    return {
        "format": 1,
        "artifactId": IMAGE,
        "planId": PLAN,
        "planDigest": DIGEST,
        "treeId": TREE,
        "treeDigest": "d" * 64,
        "reference": "example.test/owned/image:v1",
        "policy": "reviewed",
        "maximumBytes": "1073741824",
        "maximumCalls": 512,
        "timeoutSeconds": 300,
    }


def summary():
    return {
        "format": 1,
        "artifactId": IMAGE,
        "artifactDigest": "1" * 64,
        "sourceDigest": "2" * 64,
        "captureDigest": "3" * 64,
        "observedAt": DATE,
        "rootDigest": "sha256:" + "4" * 64,
        "bytes": "67108864",
        "blobCount": 6,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(**options):
    return client(charts=chart_summary(), tree=tree_summary(), image=options.pop("image", summary()), **options)


def call(sdk, *, read_only=False, value=None):
    value = body() if value is None else value
    if read_only:
        value = {key: value for key, value in value.items() if key not in protocol.WRITE_FIELDS}
    method = sdk.install_plan_image if read_only else sdk.prepare_install_plan_image
    return method(JOB, intent=INTENT, body=value, chart_artifact_id=ARTIFACT, chart_artifact_digest="c" * 64)


def test_prepare_and_cold_read_bind_original_and_all_parents_without_implicit_posts():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    assert [(method, path) for method, path, _ in calls if method != "GET"] == [
        ("POST", f"/v1/jobs/{JOB}/install-plan-image")
    ]
    reads = [query for method, path, query in calls if method == "GET" and path.endswith("/install-plan-image")]
    assert reads == [{key: str(value) for key, value in body().items() if key in protocol.BASE - {"format"}}]
    assert all(
        any(path.endswith(suffix) for _, path, _ in calls)
        for suffix in ("/install-plan", "/install-plan-charts", "/install-plan-tree")
    )


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        *[{"artifactId": value} for value in (JOB, PLAN, TREE, ARTIFACT)],
        {"reference": "https://example.test/private"},
        {"reference": "owned/image\n"},
        {"policy": "../private"},
        {"policyInput": "private.json"},
        {"server": "https://private"},
        {"maximumBytes": 1},
        {"maximumBytes": "0"},
        {"maximumBytes": str(2**63)},
        {"maximumCalls": True},
        {"maximumCalls": 4097},
        {"timeoutSeconds": 301},
        {"treeDigest": "d" * 64 + "\n"},
        {"destination": "/private"},
    ],
)
def test_invalid_inputs_refuse_before_any_peer_request(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT"
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"artifactId": str(uuid4())},
        {"continuationAuthorized": True},
        {"credentials": "PRIVATE"},
        {"sourceDigest": "not-a-digest"},
        {"captureDigest": "3" * 64 + "\n"},
        {"rootDigest": "sha256:" + "f" * 63},
        {"bytes": "1073741825"},
        {"bytes": "0"},
        {"blobCount": True},
        {"blobCount": 0},
        {"observedAt": "not-a-date"},
        {"executionState": "ready"},
    ],
)
def test_injected_private_or_invalid_summary_never_becomes_trusted(change):
    sdk, _ = peer(image={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


def test_no_capability_refuses_before_mutation():
    sdk, calls = peer(image_capability={"format": 1, "prepare": True, "readOnly": True, "continuation": True})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(method == "GET" for method, _, _ in calls)


def test_lost_post_is_not_replayed_and_explicit_get_can_recover():
    sdk, calls = peer(lost_image_reply=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            call(sdk)
        assert "PRIVATE" not in str(error.value)
        assert call(sdk, read_only=True) == summary()
    assert len([1 for method, path, _ in calls if method == "POST" and path.endswith("/install-plan-image")]) == 1


def test_changed_parent_after_collection_refuses_the_reply():
    sdk, _ = peer()
    transport = sdk._http._transport
    mutated = False

    def respond(request):
        nonlocal mutated
        response = transport.handle_request(request)
        data = response.json()
        if request.url.path.endswith("/install-plan-image"):
            mutated = True
        elif mutated and request.url.path.endswith("/install-plan"):
            data["plan"]["planDigest"] = "f" * 64
        return httpx.Response(response.status_code, json=data)

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code in {"SOURCE_CHANGED", "OPERATOR_INVALID_RESPONSE"}


def test_incomplete_historical_image_has_explicit_nonambiguous_status():
    sdk, _ = peer(image_status=409, image={"errorCode": "INSTALL_PLAN_IMAGE_INCOMPLETE"})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, read_only=True)
    assert error.value.code == "OPERATOR_IMAGE_INCOMPLETE"
    assert not error.value.ambiguous
