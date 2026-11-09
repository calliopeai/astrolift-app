from __future__ import annotations

import copy
import json
import ssl
from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan as protocol
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient
from nvke.operator_contract import CATALOG, REVISION

SITE, INSTANCE, JOB, PLAN, RUN, CLUSTER = (str(uuid4()) for _ in range(6))
DIGEST, INTENT = "a" * 64, "b" * 64
DATE = "2026-11-08T20:00:00+00:00"


def metadata():
    return {
        "format": 1,
        "itemCount": 3,
        "retainedBytes": "123",
        "proposedBytes": "456",
        "infrastructure": {"apiReady": True, "observedAt": DATE, "clusterUid": CLUSTER, "workloadCount": 1},
        "plan": {
            "format": 1,
            "scope": "remaining-management-install-proposal",
            "id": PLAN,
            "preparedAt": DATE,
            "source": {
                **dict.fromkeys(protocol.DIGEST_FIELDS, DIGEST),
                "siteId": SITE,
                "instanceId": INSTANCE,
                "jobId": JOB,
                "nativeRunId": RUN,
                "nativeRevision": REVISION,
                "nativeExitCode": 1,
                "originalStartedAt": DATE,
                "reviewObservedAt": DATE,
                "originalDeploymentEvidence": "reconstructed-prepared-deployment",
            },
            "phaseEvidence": "retained-native-stderr-order-only",
            "requiresOriginalSourceVerification": True,
            "requiresExplicitAcknowledgement": True,
            "continuationAuthorized": False,
            "firstRemainingOrdinal": 1,
            "planDigest": DIGEST,
        },
    }


def items_page():
    return {
        "format": 1,
        "view": "items",
        "planId": PLAN,
        "planDigest": DIGEST,
        "originalJobId": JOB,
        "nextCursor": None,
        "totalCount": 3,
        "items": [
            {
                "name": name,
                "type": "helm",
                "originalType": "manifest",
                "contentSha256": DIGEST,
                "originalContentSha256": DIGEST,
                "ordinal": ordinal,
                "originalObservation": observation,
                "proposedAction": "skip" if ordinal == 0 else "execute",
            }
            for ordinal, name, observation in (
                (0, "base", "returned-before-next-observed-item"),
                (1, "nktp", "last-observed-in-unsuccessful-run"),
                (2, "controller", "not-observed"),
            )
        ],
    }


def workloads_page():
    return {
        **items_page(),
        "view": "workloads",
        "totalCount": 1,
        "items": [
            {
                "namespace": "system",
                "kind": "Deployment",
                "name": "controller",
                "generation": 9007199254740993,
                "observedGeneration": None,
                "desiredReplicas": 1,
                "readyReplicas": None,
                "updatedReplicas": 0,
                "observation": "requires-review",
            }
        ],
    }


def client(
    summary=None,
    page=None,
    *,
    capability=None,
    replacement=False,
    charts=None,
    chart_capability=None,
    lost_chart_reply=False,
    changed_after_chart=False,
    tree=None,
    tree_capability=None,
    lost_tree_reply=False,
    changed_after_tree=False,
    changed_chart_after_tree=False,
    tree_status=200,
    context=None,
    context_capability=None,
    lost_context_reply=False,
    context_status=200,
    changed_after_context=False,
    verification=None,
    verification_capability=None,
    lost_verification_reply=False,
    verification_status=200,
):
    calls = []
    original = {
        "format": 2,
        "intent": INTENT,
        "revision": REVISION,
        "siteId": SITE,
        "instanceId": INSTANCE,
        "id": JOB,
        "command": "management.install",
        "state": "uncertain",
        "effectful": True,
        "artifacts": [],
        "cancellationRequested": False,
        "nativeExitCode": 1,
    }

    def respond(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/v1/identity":
            return httpx.Response(
                200,
                json={
                    "format": 1,
                    "jobFormat": 2,
                    "revision": REVISION,
                    "siteId": SITE,
                    "instanceId": str(uuid4()) if replacement and len(calls) > 2 else INSTANCE,
                    "bundleManifestSha256": DIGEST,
                    "commands": CATALOG,
                    "installPlan": capability
                    if capability is not None
                    else {
                        "format": 1,
                        "prepare": True,
                        "readOnly": True,
                        "continuation": False,
                        "maximumPageSize": 100,
                    },
                    "installPlanContext": context_capability
                    if context_capability is not None
                    else {"format": 1, "prepare": True, "readOnly": True, "continuation": False},
                    "installPlanRenderVerification": verification_capability
                    if verification_capability is not None
                    else {"format": 1, "prepare": True, "readOnly": True, "continuation": False},
                    "installPlanTree": tree_capability
                    if tree_capability is not None
                    else {"format": 1, "prepare": True, "readOnly": True, "continuation": False},
                    "installPlanCharts": chart_capability
                    if chart_capability is not None
                    else {
                        "format": 1,
                        "prepare": True,
                        "readOnly": True,
                        "continuation": False,
                    },
                },
            )
        assert request.headers["X-Astro-Nvke-Intent"] == INTENT
        if request.url.path.endswith("/install-plan-verification"):
            if lost_verification_reply and request.method == "POST":
                raise httpx.ReadError("PRIVATE_PEER_DIAGNOSTIC", request=request)
            return httpx.Response(verification_status, json=verification)
        if request.url.path.endswith("/install-plan-context"):
            if lost_context_reply and request.method == "POST":
                raise httpx.ReadError("PRIVATE_PEER_DIAGNOSTIC", request=request)
            return httpx.Response(context_status, json=context)
        if request.url.path.endswith("/install-plan-tree"):
            if lost_tree_reply and request.method == "POST":
                raise httpx.ReadError("PRIVATE_PEER_DIAGNOSTIC", request=request)
            return httpx.Response(tree_status, json=tree)
        if request.url.path.endswith("/install-plan-charts"):
            if lost_chart_reply and request.method == "POST":
                raise httpx.ReadError("PRIVATE_PEER_DIAGNOSTIC", request=request)
            selected_charts = copy.deepcopy(charts)
            if changed_chart_after_tree and any(path.endswith("/install-plan-tree") for _, path, _ in calls):
                selected_charts["expandedBytes"] = "457"
            return httpx.Response(200, json=selected_charts)
        if request.url.path.endswith("/install-plan"):
            if request.method == "POST":
                assert json.loads(request.content)["planId"] == PLAN
            value = page if request.url.params.get("view") else summary
            selected = metadata() if value is None else copy.deepcopy(value)
            if (
                (changed_after_context and any(path.endswith("/install-plan-context") for _, path, _ in calls))
                or (changed_after_chart and any(path.endswith("/install-plan-charts") for _, path, _ in calls))
                or (changed_after_tree and any(path.endswith("/install-plan-tree") for _, path, _ in calls))
            ):
                selected["plan"]["planDigest"] = "c" * 64
            return httpx.Response(200, json=selected)
        return httpx.Response(200, json=original)

    return OperatorAgentClient(
        "https://agent.example",
        site_id=SITE,
        instance_id=INSTANCE,
        bundle_manifest_sha256=DIGEST,
        verify=ssl.create_default_context(),
        transport=httpx.MockTransport(respond),
    ), calls


def test_preparation_recovery_and_pages_preserve_original_status_and_large_counts():
    sdk, calls = client(page=items_page())
    with sdk:
        assert (
            sdk.prepare_install_plan(
                JOB,
                intent=INTENT,
                body={
                    "format": 1,
                    "planId": PLAN,
                    "request": None,
                    "maximumInputBytes": "1000000",
                },
            )
            == metadata()
        )
        assert sdk.install_plan(JOB, intent=INTENT, plan_id=PLAN) == metadata()
        assert sdk.install_plan_page(JOB, intent=INTENT, plan_id=PLAN, plan_digest=DIGEST, view="items") == items_page()
    assert [path for method, path, _ in calls if method == "POST"] == [f"/v1/jobs/{JOB}/install-plan"]
    sdk, _ = client(page=workloads_page())
    with sdk:
        row = sdk.install_plan_page(JOB, intent=INTENT, plan_id=PLAN, plan_digest=DIGEST, view="workloads")["items"][0]
    assert row["generation"] == "9007199254740993" and row["readyReplicas"] is None


@pytest.mark.parametrize(
    "path,value",
    [
        (("format",), True),
        (("itemCount",), 0),
        (("retainedBytes",), "01"),
        (("plan", "continuationAuthorized"), True),
        (("plan", "firstRemainingOrdinal"), 3),
        (("plan", "source", "siteId"), str(uuid4())),
        (("plan", "source", "nativeExitCode"), 0),
        (("plan", "source", "privateSourceSha256"), "PRIVATE"),
        (("plan", "source", "nativeRevision"), "x" * 40),
        (("infrastructure", "clusterUid"), "PRIVATE"),
        (("infrastructure", "apiReady"), False),
        (("plan", "privatePayload"), "PRIVATE"),
        (("plan", "source", "environment"), {"TOKEN": "PRIVATE"}),
    ],
)
def test_malformed_or_foreign_plan_refused_without_private_diagnostics(path, value):
    data = metadata()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    sdk, _ = client(summary=data)
    with sdk, pytest.raises(NvkeError) as error:
        sdk.install_plan(JOB, intent=INTENT, plan_id=PLAN)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"planDigest": "c" * 64},
        {"originalJobId": str(uuid4())},
        {"view": "workloads"},
        {"totalCount": 4},
        {"nextCursor": ""},
        {"items": []},
        {"items": items_page()["items"][:1]},
        {"items": [{**items_page()["items"][1], "proposedAction": "skip"}]},
        {"items": [items_page()["items"][1], items_page()["items"][0]]},
        {"items": [{**items_page()["items"][0], "privatePath": "PRIVATE"}]},
    ],
)
def test_invalid_pages_cannot_change_original_binding_or_proposed_actions(change):
    sdk, _ = client(page={**items_page(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        sdk.install_plan_page(JOB, intent=INTENT, plan_id=PLAN, plan_digest=DIGEST, view="items")
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


def test_current_identity_capability_and_digest_required_before_page_exposure():
    for kwargs in ({"capability": {"format": 1}}, {"replacement": True}):
        sdk, _ = client(**kwargs)
        with sdk, pytest.raises(NvkeError):
            sdk.install_plan(JOB, intent=INTENT, plan_id=PLAN)
    sdk, calls = client()
    with sdk, pytest.raises(NvkeError) as error:
        sdk.install_plan_page(JOB, intent=INTENT, plan_id=PLAN, plan_digest="c" * 64, view="items")
    assert error.value.code == "SOURCE_CHANGED"
    assert not any(params.get("view") for _, _, params in calls)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"format": True, "planId": PLAN, "request": None, "maximumInputBytes": "10"},
        {"format": 1, "planId": PLAN, "request": None, "maximumInputBytes": "0"},
        {
            "format": 1,
            "planId": PLAN,
            "request": None,
            "maximumInputBytes": "10",
            "overrides": {"options": {"PRIVATE": None}},
        },
    ],
)
def test_invalid_preparation_input_never_reaches_peer(body):
    sdk, calls = client()
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan(JOB, intent=INTENT, body=copy.deepcopy(body))
    assert error.value.code == "INVALID_INPUT" and not calls


@pytest.mark.parametrize("changes", [{"readyReplicas": True}, {"generation": 2**63}, {"observation": "ready"}])
def test_workload_precision_and_readiness_are_checked(changes):
    data = workloads_page()
    data["items"][0].update(changes)
    sdk, _ = client(page=data)
    with sdk, pytest.raises(NvkeError):
        sdk.install_plan_page(JOB, intent=INTENT, plan_id=PLAN, plan_digest=DIGEST, view="workloads")
