from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_workload as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, DIGEST, INTENT, JOB, PLAN, client, metadata
from tests.nvke.test_operator_install_plan_charts import ARTIFACT
from tests.nvke.test_operator_install_plan_charts import summary as chart_summary
from tests.nvke.test_operator_install_plan_image import body as image_body
from tests.nvke.test_operator_install_plan_image import call as image_call
from tests.nvke.test_operator_install_plan_image import summary as image_summary
from tests.nvke.test_operator_install_plan_tree import TREE
from tests.nvke.test_operator_install_plan_tree import summary as tree_summary

WORKLOAD = str(uuid4())


def body():
    return {
        "format": 1,
        "artifactId": WORKLOAD,
        "planId": PLAN,
        "planDigest": DIGEST,
        "treeId": TREE,
        "treeDigest": "d" * 64,
        "contextBindings": {"owned": {"contextId": str(uuid4()), "contextDigest": "c" * 64}},
        "maximumBytes": "67108864",
        "maximumResources": 10000,
        "timeoutSeconds": 300,
    }


def summary():
    return {
        "format": 1,
        "artifactId": WORKLOAD,
        "artifactDigest": "1" * 64,
        "sourceDigest": "2" * 64,
        "captureDigest": "3" * 64,
        "workloadDigest": "4" * 64,
        "observedAt": DATE,
        "itemCount": metadata()["itemCount"],
        "resourceCount": 2,
        "imageUseCount": 3,
        "imageReferenceCount": 1,
        "issueCount": 0,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(**options):
    return client(charts=chart_summary(), tree=tree_summary(), workload=options.pop("workload", summary()), **options)


def call(sdk, *, read_only=False, value=None):
    value = body() if value is None else value
    if read_only:
        value = {key: value for key, value in value.items() if key in protocol.BASE}
    method = sdk.install_plan_workload if read_only else sdk.prepare_install_plan_workload
    return method(JOB, intent=INTENT, body=value, chart_artifact_id=ARTIFACT, chart_artifact_digest="c" * 64)


def test_prepare_and_offline_recovery_only_post_explicit_preparation():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    assert [(method, path) for method, path, _ in calls if method != "GET"] == [
        ("POST", f"/v1/jobs/{JOB}/install-plan-workload-inventory")
    ]
    query = next(
        query for method, path, query in calls if method == "GET" and path.endswith("/install-plan-workload-inventory")
    )
    assert set(query) == protocol.BASE - {"format"}


def test_lost_reply_requires_explicit_recovery_without_replay():
    sdk, calls = peer(lost_workload_reply=True)
    with sdk:
        with pytest.raises(NvkeError):
            call(sdk)
        assert call(sdk, read_only=True) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"kubeconfig": "/private"},
        {"maximumBytes": "67108865"},
        {"maximumResources": True},
        {"timeoutSeconds": 301},
        {"contextBindings": {"../private": {"contextId": str(uuid4()), "contextDigest": "c" * 64}}},
        {"contextBindings": {"owned": {"contextId": PLAN, "contextDigest": "c" * 64}}},
    ],
)
def test_invalid_inventory_requests_refuse_before_transport(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT"
    assert not calls


@pytest.mark.parametrize(
    "change",
    [
        {"credentials": "PRIVATE"},
        {"continuationAuthorized": True},
        {"itemCount": 0},
        {"resourceCount": 10001},
        {"imageReferenceCount": 4},
        {"workloadDigest": "invalid"},
        {"imageUseCount": True},
        {"observedAt": "invalid"},
    ],
)
def test_invalid_observation_never_becomes_trusted(change):
    sdk, _ = peer(workload={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


def test_missing_inventory_capability_refuses_before_post():
    sdk, calls = peer(workload_capability={})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(method == "GET" for method, _, _ in calls)


def test_workload_linked_image_retains_inventory_on_write_and_cold_read():
    linked = {"workloadId": WORKLOAD, "workloadDigest": summary()["workloadDigest"]}
    expected = {**image_summary(), "sourceKind": "registry", **linked}
    sdk, calls = peer(image=expected)
    with sdk:
        assert image_call(sdk, value={**image_body(), **linked}) == expected
        assert image_call(sdk, value={**image_body(), **linked}, read_only=True) == expected
    assert all(method == "GET" for method, path, _ in calls if path.endswith("/install-plan-workload-inventory"))
    assert sum(method == "POST" for method, _, _ in calls) == 1


def test_inventory_replacement_refuses_linked_image_before_mutation():
    linked = {"workloadId": WORKLOAD, "workloadDigest": "f" * 64}
    sdk, calls = peer(image={**image_summary(), "sourceKind": "registry", **linked})
    with sdk, pytest.raises(NvkeError) as error:
        image_call(sdk, value={**image_body(), **linked})
    assert error.value.code == "SOURCE_CHANGED"
    assert all(method == "GET" for method, _, _ in calls)


def test_inventory_change_after_image_reply_refuses_result():
    linked = {"workloadId": WORKLOAD, "workloadDigest": summary()["workloadDigest"]}
    sdk, _ = peer(image={**image_summary(), "sourceKind": "registry", **linked})
    transport = sdk._http._transport
    mutated = False

    def respond(request):
        nonlocal mutated
        response = transport.handle_request(request)
        data = response.json()
        if request.url.path.endswith("/install-plan-image"):
            mutated = True
        elif mutated and request.url.path.endswith("/install-plan-workload-inventory"):
            data["workloadDigest"] = "f" * 64
        return httpx.Response(response.status_code, json=data)

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError) as error:
        image_call(sdk, value={**image_body(), **linked})
    assert error.value.code == "SOURCE_CHANGED"


@pytest.mark.parametrize(
    "change", [{"workloadId": WORKLOAD}, {"workloadDigest": "4" * 64}, {"workloadId": PLAN, "workloadDigest": "4" * 64}]
)
def test_partial_or_aliasing_image_scope_refuses_before_transport(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        image_call(sdk, value={**image_body(), **change})
    assert error.value.code == "INVALID_INPUT"
    assert not calls
