from __future__ import annotations

import copy
from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_immutable as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, DIGEST, INTENT, JOB, PLAN, metadata
from tests.nvke.test_operator_install_plan_charts import ARTIFACT
from tests.nvke.test_operator_install_plan_image_binding import BINDING
from tests.nvke.test_operator_install_plan_image_binding import summary as binding_summary
from tests.nvke.test_operator_install_plan_tree import TREE
from tests.nvke.test_operator_install_plan_workload import WORKLOAD, peer
from tests.nvke.test_operator_install_plan_workload import summary as workload_summary

PAYLOADS = {name: str(uuid4()) for name in ("completed", "NKTP_One", "native")}
EXECUTION = str(uuid4())
SOURCE = {
    "chart_artifact_id": ARTIFACT,
    "chart_artifact_digest": "c" * 64,
    "workload_id": WORKLOAD,
    "workload_digest": workload_summary()["workloadDigest"],
}


def payload_summary(artifact_id):
    return {
        "format": 1,
        "artifactId": artifact_id,
        "artifactDigest": "5" * 64,
        "sourceDigest": "6" * 64,
        "captureDigest": "7" * 64,
        "payloadDigest": "8" * 64,
        "observedAt": DATE,
        "sourceObservedAt": DATE,
        "fileCount": 1,
        "payloadBytes": "1234",
        "resourceCount": 1,
        "imageUseCount": 1,
        "rewriteCount": 1,
        "executionState": protocol.PAYLOAD_STATE,
        "continuationAuthorized": False,
    }


def tree_summary():
    return {
        "format": 1,
        "artifactId": EXECUTION,
        "artifactDigest": "5" * 64,
        "sourceDigest": "6" * 64,
        "captureDigest": "7" * 64,
        "executionDigest": "8" * 64,
        "observedAt": DATE,
        "sourceObservedAt": DATE,
        "fileCount": 8,
        "bytes": "5678",
        "phaseCount": metadata()["itemCount"] - metadata()["plan"]["firstRemainingOrdinal"],
        "skippedPhaseCount": metadata()["plan"]["firstRemainingOrdinal"],
        "importCount": 0,
        "executionState": protocol.TREE_STATE,
        "continuationAuthorized": False,
    }


def selections():
    return {
        name: {"artifactId": artifact_id, "artifactDigest": payload_summary(artifact_id)["artifactDigest"]}
        for name, artifact_id in PAYLOADS.items()
    }


def body(*, tree=False):
    value = {
        "format": 1,
        "artifactId": EXECUTION if tree else PAYLOADS["NKTP_One"],
        "planId": PLAN,
        "planDigest": DIGEST,
        "treeId": TREE,
        "treeDigest": "d" * 64,
        "bindingId": BINDING,
        "bindingDigest": binding_summary()["bindingDigest"],
        "maximumBytes": "1048576",
        "timeoutSeconds": 300,
    }
    if tree:
        value["payloadBindings"] = selections()
    else:
        value.update(item="NKTP_One", maximumResources=100)
    return value


def client(**options):
    return peer(
        image_binding=options.pop("image_binding", binding_summary()),
        image_payloads=options.pop("image_payloads", {v: payload_summary(v) for v in PAYLOADS.values()}),
        immutable_execution=options.pop("immutable_execution", tree_summary()),
        **options,
    )


def call(sdk, *, tree=False, read_only=False, value=None):
    value = body(tree=tree) if value is None else value
    if read_only:
        value = {k: v for k, v in value.items() if k in protocol.BASE | (set() if tree else {"item"})}
    options = {**SOURCE}
    if tree and read_only:
        options["payload_bindings"] = selections()
    method = (
        sdk.install_plan_immutable_execution_tree
        if tree and read_only
        else sdk.prepare_install_plan_immutable_execution_tree
        if tree
        else sdk.install_plan_image_payload
        if read_only
        else sdk.prepare_install_plan_image_payload
    )
    return method(JOB, intent=INTENT, body=value, **options)


@pytest.mark.parametrize("tree", [False, True])
def test_prepare_and_explicit_recovery_preserve_exact_closed_metadata(tree):
    sdk, calls = client()
    expected = tree_summary() if tree else payload_summary(PAYLOADS["NKTP_One"])
    with sdk:
        assert call(sdk, tree=tree) == expected
        assert call(sdk, tree=tree, read_only=True) == expected
    assert sum(method == "POST" for method, _, _ in calls) == 1
    assert all("imageBindings" not in q and "helperProgram" not in q for _, _, q in calls)


@pytest.mark.parametrize("tree", [False, True])
def test_lost_reply_never_retries_preparation_automatically(tree):
    sdk, calls = client(**{"lost_immutable_execution_reply" if tree else "lost_image_payload_reply": True})
    with sdk:
        with pytest.raises(NvkeError) as error:
            call(sdk, tree=tree)
        assert error.value.ambiguous and not error.value.retryable
        assert "PRIVATE" not in str(error.value)
        assert call(sdk, tree=tree, read_only=True) == (
            tree_summary() if tree else payload_summary(PAYLOADS["NKTP_One"])
        )
    assert sum(method == "POST" for method, _, _ in calls) == 1


@pytest.mark.parametrize("tree", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"maximumBytes": 1},
        {"maximumBytes": "01"},
        {"maximumBytes": "9223372036854775808"},
        {"timeoutSeconds": True},
        {"helperProgram": "/PRIVATE"},
        {"continuationAuthorized": True},
        {"artifactId": WORKLOAD},
        {"bindingDigest": "bad"},
    ],
)
def test_invalid_inputs_refuse_before_transport(tree, change):
    sdk, calls = client()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, tree=tree, value={**body(tree=tree), **change})
    assert error.value.code == "INVALID_INPUT" and not calls


@pytest.mark.parametrize(
    "change",
    [
        {"credentials": "PRIVATE"},
        {"continuationAuthorized": True},
        {"fileCount": 0},
        {"fileCount": True},
        {"resourceCount": 3},
        {"imageUseCount": 4},
        {"rewriteCount": 2},
        {"sourceObservedAt": "2020-01-01T00:00:00+00:00"},
        {"payloadDigest": "bad"},
        {"payloadBytes": "1048577"},
    ],
)
def test_payload_observation_is_bounded_to_binding_and_workload(change):
    responses = {v: payload_summary(v) for v in PAYLOADS.values()}
    responses[PAYLOADS["NKTP_One"]].update(change)
    sdk, _ = client(image_payloads=responses)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"credentials": "PRIVATE"},
        {"continuationAuthorized": True},
        {"phaseCount": 3},
        {"skippedPhaseCount": 0},
        {"importCount": 2},
        {"executionDigest": "bad"},
        {"fileCount": True},
        {"bytes": "1048577"},
    ],
)
def test_sealed_tree_counts_and_observations_remain_original_plan_bound(change):
    sdk, _ = client(immutable_execution={**tree_summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, tree=True)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("tree", [False, True])
def test_missing_capability_and_changed_binding_refuse_before_post(tree):
    for options, code in (
        (
            {"immutable_execution_capability" if tree else "image_payload_capability": {}},
            "OPERATOR_CONTROL_UNSUPPORTED",
        ),
        ({"image_binding": {**binding_summary(), "bindingDigest": "f" * 64}}, "SOURCE_CHANGED"),
    ):
        sdk, calls = client(**options)
        with sdk, pytest.raises(NvkeError) as error:
            call(sdk, tree=tree)
        assert error.value.code == code
        assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("tree", [False, True])
def test_binding_replacement_after_response_is_not_published(tree):
    sdk, _ = client()
    transport = sdk._http._transport
    mutated = False
    ending = "/install-plan-immutable-execution-tree" if tree else "/install-plan-image-payload"

    def respond(request):
        nonlocal mutated
        response = transport.handle_request(request)
        data = copy.deepcopy(response.json())
        if request.method == "POST" and request.url.path.endswith(ending):
            mutated = True
        elif mutated and request.url.path.endswith("/install-plan-image-binding"):
            data["bindingDigest"] = "f" * 64
        return httpx.Response(response.status_code, json=data)

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, tree=tree)
    assert error.value.code == "SOURCE_CHANGED"


def test_selected_payload_replacement_after_tree_response_is_refused():
    sdk, _ = client()
    transport = sdk._http._transport
    mutated = False

    def respond(request):
        nonlocal mutated
        response = transport.handle_request(request)
        data = copy.deepcopy(response.json())
        if request.method == "POST" and request.url.path.endswith("/install-plan-immutable-execution-tree"):
            mutated = True
        elif mutated and request.url.path.endswith("/install-plan-image-payload"):
            data["artifactDigest"] = "f" * 64
        return httpx.Response(response.status_code, json=data)

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, tree=True)
    assert error.value.code == "SOURCE_CHANGED"


@pytest.mark.parametrize(
    "selected",
    [
        {},
        {"../PRIVATE": {"artifactId": str(uuid4()), "artifactDigest": "d" * 64}},
        {"one": {"artifactId": BINDING, "artifactDigest": "d" * 64}},
        {"one": {"artifactId": str(uuid4()), "artifactDigest": True}},
        {"one": {"artifactId": str(uuid4()), "artifactDigest": "d" * 64, "path": "/PRIVATE"}},
    ],
)
def test_invalid_phase_selections_refuse_before_transport(selected):
    sdk, calls = client()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, tree=True, value={**body(tree=True), "payloadBindings": selected})
    assert error.value.code == "INVALID_INPUT" and not calls


def test_duplicate_phase_identities_refuse_before_transport():
    sdk, calls = client()
    row = {"artifactId": str(uuid4()), "artifactDigest": "d" * 64}
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, tree=True, value={**body(tree=True), "payloadBindings": {"one": row, "two": dict(row)}})
    assert error.value.code == "INVALID_INPUT" and not calls


def test_read_only_capability_does_not_authorize_preparation():
    capability = {"format": 1, "prepare": False, "readOnly": True, "continuation": False}
    sdk, calls = client(image_payload_capability=capability)
    with sdk:
        assert call(sdk, read_only=True) == payload_summary(PAYLOADS["NKTP_One"])
        with pytest.raises(NvkeError) as error:
            call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(method == "GET" for method, _, _ in calls)
