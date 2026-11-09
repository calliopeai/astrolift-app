from __future__ import annotations

from collections import Counter
from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_verification as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, DIGEST, INTENT, JOB, PLAN, client, items_page
from tests.nvke.test_operator_install_plan_charts import ARTIFACT
from tests.nvke.test_operator_install_plan_charts import summary as chart_summary
from tests.nvke.test_operator_install_plan_context import CONTEXT
from tests.nvke.test_operator_install_plan_context import summary as context_summary
from tests.nvke.test_operator_install_plan_tree import TREE
from tests.nvke.test_operator_install_plan_tree import summary as tree_summary

VERIFICATION = str(uuid4())


def body():
    return {
        "format": 1,
        "artifactId": VERIFICATION,
        "contextId": CONTEXT,
        "contextDigest": "3" * 64,
        "planId": PLAN,
        "planDigest": DIGEST,
        "treeId": TREE,
        "treeDigest": "d" * 64,
        "item": "nktp",
        "maximumBytes": "16777216",
        "maximumCaptureAgeSeconds": 3600,
        "maximumCalls": 512,
        "timeoutSeconds": 300,
    }


def summary():
    return {
        **{k: v for k, v in body().items() if k not in protocol.WRITE_FIELDS},
        "artifactDigest": "4" * 64,
        "chartArtifactId": ARTIFACT,
        "chartArtifactDigest": "c" * 64,
        "ordinal": 1,
        "captureDigest": "1" * 64,
        "frozenChartSha256": "2" * 64,
        "verificationDigest": "5" * 64,
        "observedAt": DATE,
        "requests": 3,
        "bytes": "123",
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(**options):
    return client(
        page=items_page(),
        charts=chart_summary(),
        tree=tree_summary(),
        context=context_summary(),
        verification=options.pop("verification", summary()),
        **options,
    )


def call(sdk, *, read_only=False, value=None):
    value = body() if value is None else value
    if read_only:
        value = {k: v for k, v in value.items() if k not in protocol.WRITE_FIELDS}
    method = sdk.install_plan_verification if read_only else sdk.prepare_install_plan_verification
    return method(JOB, intent=INTENT, body=value, chart_artifact_id=ARTIFACT, chart_artifact_digest="c" * 64)


def intercept(sdk, change):
    original = sdk._http._transport

    def respond(request):
        response = original.handle_request(request)
        return httpx.Response(response.status_code, json=change(request, response.json()))

    sdk._http._transport = httpx.MockTransport(respond)


def test_prepare_and_historical_read_bind_complete_source_with_bounded_reads():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
        first = list(calls)
        assert call(sdk, read_only=True) == summary()
    assert [(m, p) for m, p, _ in calls if m != "GET"] == [("POST", f"/v1/jobs/{JOB}/install-plan-verification")]
    counts = Counter(p.rsplit("/", 1)[-1] for _, p, _ in first)
    assert counts["install-plan"] == 4
    assert all(counts[name] == 2 for name in ("install-plan-charts", "install-plan-tree", "install-plan-context"))
    reads = [q for m, p, q in calls if m == "GET" and p.endswith("/install-plan-verification")]
    assert reads == [{k: str(v) for k, v in body().items() if k != "format" and k not in protocol.WRITE_FIELDS}]


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        *[{"artifactId": x} for x in (JOB, PLAN, TREE, CONTEXT, ARTIFACT)],
        {"contextDigest": "PRIVATE"},
        {"contextId": "../PRIVATE"},
        {"maximumBytes": 1},
        {"maximumBytes": "0"},
        {"maximumBytes": "01"},
        {"maximumBytes": "536870913"},
        {"maximumCalls": True},
        {"maximumCalls": 2049},
        {"maximumCaptureAgeSeconds": 0},
        {"maximumCaptureAgeSeconds": 3601},
        {"timeoutSeconds": 301},
        {"item": "../PRIVATE"},
        {"credentials": "PRIVATE"},
        {"continuationAuthorized": True},
    ],
)
def test_invalid_requests_never_contact_agent(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT" and "PRIVATE" not in str(error.value)
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"artifactId": str(uuid4())},
        {"contextId": str(uuid4())},
        {"contextDigest": "f" * 64},
        {"artifactDigest": "PRIVATE"},
        {"verificationDigest": None},
        {"planDigest": "f" * 64},
        {"chartArtifactId": str(uuid4())},
        {"chartArtifactDigest": "f" * 64},
        {"treeDigest": "f" * 64},
        {"item": "controller"},
        {"ordinal": True},
        {"ordinal": 2},
        {"captureDigest": "f" * 64},
        {"frozenChartSha256": "f" * 64},
        {"requests": True},
        {"requests": 513},
        {"bytes": "16777217"},
        {"bytes": "0"},
        {"bytes": 123},
        {"observedAt": "2026-11-09T00:00:00"},
        {"executionState": "ready"},
        {"continuationAuthorized": 0},
        {"capture": "PRIVATE"},
    ],
)
def test_malformed_private_or_unbound_reply_is_redacted(change):
    sdk, _ = peer(verification={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "key,value", [("contextDigest", "e" * 64), ("treeDigest", "e" * 64), ("planDigest", "e" * 64), ("item", "missing")]
)
def test_changed_reviewed_source_refuses_before_post(key, value):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), key: value})
    assert error.value.code == "SOURCE_CHANGED"
    assert not any(m == "POST" for m, _, _ in calls)


def test_missing_capability_refuses_before_source_collection():
    sdk, calls = peer(verification_capability={"format": 1, "continuation": True})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert len(calls) == 1


@pytest.mark.parametrize("node", ["plan", "chart", "tree", "context", "item", "original", "capability"])
def test_every_source_node_drift_after_verification_refuses(node):
    sdk, calls = peer()

    def change(request, data):
        if not any(p.endswith("/install-plan-verification") for _, p, _ in calls):
            return data
        path = request.url.path
        if node == "plan" and path.endswith("/install-plan") and not request.url.params.get("view"):
            data["proposedBytes"] = "457"
        elif node == "chart" and path.endswith("/install-plan-charts"):
            data["expandedBytes"] = "457"
        elif node == "tree" and path.endswith("/install-plan-tree"):
            data["fileCount"] = 8
        elif node == "context" and path.endswith("/install-plan-context"):
            data["observedAt"] = "2026-11-08T20:00:01+00:00"
        elif node == "item" and request.url.params.get("view"):
            data["items"][1]["contentSha256"] = "e" * 64
        elif node == "original" and path.endswith(JOB):
            data["cancellationRequested"] = True
        elif node == "capability" and path == "/v1/identity":
            data["installPlanRenderVerification"]["continuation"] = True
        return data

    intercept(sdk, change)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert sum(m == "POST" for m, _, _ in calls) == 1


def test_lost_reply_is_ambiguous_and_explicit_read_does_not_replay():
    sdk, calls = peer(lost_verification_reply=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            call(sdk)
        assert error.value.ambiguous and error.value.code == "OPERATOR_UNAVAILABLE"
        assert call(sdk, read_only=True) == summary()
    assert sum(m == "POST" for m, _, _ in calls) == 1


def test_incomplete_response_is_stable_and_private_detail_is_redacted():
    sdk, _ = peer(
        verification_status=409, verification={"errorCode": "INSTALL_PLAN_VERIFICATION_INCOMPLETE", "detail": "PRIVATE"}
    )
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, read_only=True)
    assert error.value.code == "OPERATOR_VERIFICATION_INCOMPLETE" and "PRIVATE" not in str(error.value)


def test_actor_revocation_after_source_read_prevents_post():
    sdk, calls = peer()

    def checkpoint():
        if any(p.endswith("/install-plan-context") for _, p, _ in calls):
            raise NvkeError("FORBIDDEN", "Authority revoked")

    sdk._checkpoint = checkpoint
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "FORBIDDEN"
    assert not any(m == "POST" for m, _, _ in calls)


def test_read_body_refuses_write_policy():
    with pytest.raises(ValueError):
        protocol.request(body(), job_id=JOB, chart_artifact_id=ARTIFACT, read_only=True)


@pytest.mark.parametrize(
    "fault", [None, "duplicate-name", "duplicate-ordinal", "changed-total", "missing-tail", "loop-cursor"]
)
def test_paginated_source_is_complete_ordered_and_stable(fault):
    sdk, calls = peer()
    template = items_page()["items"][1]

    def change(request, data):
        if not request.url.path.endswith("/install-plan"):
            return data
        if not request.url.params.get("view"):
            data["itemCount"] = 101
            return data
        tail = bool(request.url.params.get("after"))
        rows = [
            {
                **template,
                "name": "nktp" if i == 1 else f"phase-{i}",
                "ordinal": i,
                "proposedAction": "skip" if i == 0 else "execute",
                "originalObservation": "returned-before-next-observed-item"
                if i == 0
                else "last-observed-in-unsuccessful-run"
                if i == 1
                else "not-observed",
            }
            for i in range(101)
        ]
        data.update(totalCount=101, items=rows[100:] if tail else rows[:100], nextCursor=None if tail else "cursor")
        if tail and fault == "duplicate-name":
            data["items"][0]["name"] = "nktp"
        if tail and fault == "duplicate-ordinal":
            data["items"][0]["ordinal"] = 99
        if tail and fault == "changed-total":
            data["totalCount"] = 100
        if tail and fault == "missing-tail":
            data["items"] = []
        if tail and fault == "loop-cursor":
            data.update(items=rows[:100], nextCursor="cursor")
        return data

    intercept(sdk, change)
    with sdk:
        if fault is None:
            assert call(sdk) == summary()
        else:
            with pytest.raises(NvkeError) as error:
                call(sdk)
            assert error.value.code == "OPERATOR_INVALID_RESPONSE"
            assert not any(m == "POST" for m, _, _ in calls)


@pytest.mark.parametrize("node", ["capability", "original"])
def test_drift_during_final_context_read_is_checked_before_post(node):
    sdk, calls = peer()

    def change(request, data):
        if any(p.endswith("/install-plan-context") for _, p, _ in calls):
            if node == "capability" and request.url.path == "/v1/identity":
                data["installPlanRenderVerification"]["continuation"] = True
            if node == "original" and request.url.path.endswith(JOB):
                data["cancellationRequested"] = True
        return data

    intercept(sdk, change)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert not any(m == "POST" for m, _, _ in calls)
