from __future__ import annotations

from uuid import uuid4

import pytest

from nvke import _operator_install_plan_context as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, DIGEST, INTENT, JOB, PLAN, client, items_page
from tests.nvke.test_operator_install_plan_charts import ARTIFACT
from tests.nvke.test_operator_install_plan_charts import summary as chart_summary
from tests.nvke.test_operator_install_plan_tree import TREE
from tests.nvke.test_operator_install_plan_tree import summary as tree_summary

CONTEXT = str(uuid4())


def body():
    return {
        "format": 1,
        "artifactId": CONTEXT,
        "planId": PLAN,
        "planDigest": DIGEST,
        "treeId": TREE,
        "treeDigest": "d" * 64,
        "item": "nktp",
        "maximumBytes": "16777216",
        "maximumArtifactBytes": "1048576",
        "revision": "1",
        "upgrade": False,
        "maximumCalls": 512,
        "maximumIterations": 64,
        "timeoutSeconds": 300,
    }


def summary():
    return {
        **{k: v for k, v in body().items() if k not in protocol.WRITE_FIELDS},
        "ordinal": 1,
        "captureDigest": "1" * 64,
        "frozenChartSha256": "2" * 64,
        "contextDigest": "3" * 64,
        "documentCount": 1,
        "lookupCount": 0,
        "observedAt": DATE,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(**options):
    return client(
        page=items_page(),
        charts=chart_summary(),
        tree=tree_summary(),
        context=options.pop("context", summary()),
        **options,
    )


def call(sdk, *, read_only=False, value=None, intent=INTENT):
    value = body() if value is None else value
    if read_only:
        value = {k: v for k, v in value.items() if k not in protocol.WRITE_FIELDS}
    method = sdk.install_plan_context if read_only else sdk.prepare_install_plan_context
    return method(JOB, intent=intent, body=value, chart_artifact_id=ARTIFACT, chart_artifact_digest="c" * 64)


def test_prepare_and_explicit_read_bind_parents_item_and_ordinal_without_private_data():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    writes = [(m, p) for m, p, _ in calls if m != "GET"]
    assert writes == [("POST", f"/v1/jobs/{JOB}/install-plan-context")]
    reads = [q for m, p, q in calls if m == "GET" and p.endswith("/install-plan-context")]
    assert reads == [{k: str(v) for k, v in body().items() if k != "format" and k not in protocol.WRITE_FIELDS}]


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"artifactId": JOB},
        {"artifactId": PLAN},
        {"artifactId": TREE},
        {"artifactId": ARTIFACT},
        {"treeId": JOB},
        {"treeDigest": "PRIVATE"},
        {"item": "../PRIVATE"},
        {"helperProgram": "PRIVATE"},
        {"schemas": {}},
        {"read_response": "PRIVATE"},
        {"maximumBytes": 123},
        {"maximumBytes": "0"},
        {"maximumBytes": "536870913"},
        {"maximumArtifactBytes": "65535"},
        {"maximumArtifactBytes": "134217729"},
        {"revision": "0"},
        {"revision": "01"},
        {"revision": 1},
        {"revision": "9223372036854775808"},
        {"revision": "2"},
        {"upgrade": 1},
        {"maximumCalls": True},
        {"maximumCalls": 2049},
        {"maximumIterations": 65},
        {"timeoutSeconds": 301},
        {"continuationAuthorized": True},
    ],
)
def test_invalid_requests_do_not_contact_agent(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT" and "PRIVATE" not in str(error.value)
    assert calls == []


def test_upgrade_revision_preserves_large_decimal_and_get_rejects_write_fields():
    value = {**body(), "upgrade": True, "revision": "9007199254740993"}
    assert protocol.request(value, job_id=JOB, read_only=False, chart_artifact_id=ARTIFACT) == value
    with pytest.raises(ValueError):
        protocol.request(value, job_id=JOB, read_only=True, chart_artifact_id=ARTIFACT)


@pytest.mark.parametrize("name", ["NVKE_phase-1", "A" * 128])
def test_native_item_names_preserve_uppercase_underscores_and_original_bound(name):
    value = {**body(), "item": name}
    assert protocol.request(value, job_id=JOB, read_only=False, chart_artifact_id=ARTIFACT) == value


@pytest.mark.parametrize("name", ["a" * 129, "lower/child", "-prefix", "suffix\n"])
def test_invalid_native_names_refuse(name):
    with pytest.raises(ValueError):
        protocol.request({**body(), "item": name}, job_id=JOB, read_only=False, chart_artifact_id=ARTIFACT)


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"artifactId": str(uuid4())},
        {"planDigest": "f" * 64},
        {"treeDigest": "f" * 64},
        {"item": "controller"},
        {"ordinal": 2},
        {"ordinal": True},
        {"captureDigest": "PRIVATE"},
        {"frozenChartSha256": "PRIVATE"},
        {"contextDigest": None},
        {"documentCount": -1},
        {"lookupCount": 2049},
        {"observedAt": "2026-11-09T00:00:00"},
        {"executionState": "ready"},
        {"continuationAuthorized": True},
        {"capture": "PRIVATE"},
    ],
)
def test_private_malformed_or_unbound_replies_refuse(change):
    sdk, _ = peer(context={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE" not in str(error.value)


def test_missing_capability_refuses_before_context_post():
    sdk, calls = peer(context_capability={"format": 1, "continuation": True})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert not any(p.endswith("/install-plan-context") for _, p, _ in calls)


def test_wrong_tree_digest_refuses_before_context_post():
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), "treeDigest": "e" * 64})
    assert error.value.code == "SOURCE_CHANGED"
    assert not any(p.endswith("/install-plan-context") for _, p, _ in calls)


def test_parent_drift_after_capture_does_not_certify_context():
    sdk, calls = peer(changed_after_context=True)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "SOURCE_CHANGED"
    assert sum(m == "POST" for m, _, _ in calls) == 1


def test_missing_helm_item_refuses_before_post():
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), "item": "missing"})
    assert error.value.code == "SOURCE_CHANGED"
    assert not any(p.endswith("/install-plan-context") for _, p, _ in calls)


def test_lost_post_is_ambiguous_and_same_id_recovers_without_automatic_replay():
    sdk, calls = peer(lost_context_reply=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            call(sdk)
        assert error.value.code == "OPERATOR_UNAVAILABLE" and error.value.ambiguous
        assert call(sdk, read_only=True) == summary()
    assert sum(m == "POST" for m, _, _ in calls) == 1


def test_incomplete_context_maps_to_stable_redacted_error():
    sdk, _ = peer(context_status=409, context={"errorCode": "INSTALL_PLAN_CONTEXT_INCOMPLETE", "detail": "PRIVATE"})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, read_only=True)
    assert error.value.code == "OPERATOR_CONTEXT_INCOMPLETE" and "PRIVATE" not in str(error.value)
