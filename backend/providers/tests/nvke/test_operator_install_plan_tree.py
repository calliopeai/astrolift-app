from __future__ import annotations

from uuid import uuid4

import pytest

from nvke import _operator_install_plan_tree as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DIGEST, INTENT, JOB, PLAN, client
from tests.nvke.test_operator_install_plan_charts import ARTIFACT
from tests.nvke.test_operator_install_plan_charts import summary as chart_summary

TREE = str(uuid4())


def body():
    return {
        "format": 1,
        "artifactId": TREE,
        "planId": PLAN,
        "planDigest": DIGEST,
        "chartArtifactId": ARTIFACT,
        "chartArtifactDigest": "c" * 64,
        "maximumBytes": "9007199254740993",
    }


def summary():
    return {
        "format": 1,
        "artifactId": TREE,
        "planId": PLAN,
        "planDigest": DIGEST,
        "chartArtifactId": ARTIFACT,
        "chartArtifactDigest": "c" * 64,
        "treeDigest": "d" * 64,
        "fileCount": 7,
        "bytes": "9007199254740993",
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(**changes):
    return client(charts=chart_summary(), tree=summary(), **changes)


def read(sdk):
    return sdk.install_plan_tree(
        JOB,
        intent=INTENT,
        plan_id=PLAN,
        plan_digest=DIGEST,
        chart_artifact_id=ARTIFACT,
        chart_artifact_digest="c" * 64,
        artifact_id=TREE,
    )


def tree_calls(calls):
    return [(method, path, query) for method, path, query in calls if path.endswith("/install-plan-tree")]


def test_prepare_and_explicit_read_bind_both_publications_and_preserve_decimal_bytes():
    sdk, calls = peer()
    with sdk:
        assert sdk.prepare_install_plan_tree(JOB, intent=INTENT, body=body()) == summary()
        assert read(sdk) == summary()
    assert [path for method, path, _ in calls if method != "GET"] == [f"/v1/jobs/{JOB}/install-plan-tree"]
    assert tree_calls(calls)[1][2] == {
        key: value for key, value in body().items() if key not in {"format", "maximumBytes"}
    }
    assert sum(path.endswith("/install-plan-charts") for _, path, _ in calls) >= 4


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"artifactId": JOB},
        {"artifactId": PLAN},
        {"artifactId": ARTIFACT},
        {"planId": JOB},
        {"chartArtifactId": PLAN},
        {"chartArtifactId": JOB},
        {"artifactId": "../outside"},
        {"planDigest": None},
        {"chartArtifactDigest": "PRIVATE"},
        {"maximumBytes": 123},
        {"maximumBytes": True},
        {"maximumBytes": "0"},
        {"maximumBytes": "01"},
        {"maximumBytes": "9223372036854775808"},
        {"maximumBytes": None},
        {"timeoutSeconds": 300},
        {"executable": "PRIVATE"},
        {"continuationAuthorized": True},
    ],
)
def test_invalid_tree_requests_never_contact_the_agent(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_tree(JOB, intent=INTENT, body={**body(), **change})
    assert error.value.code == "INVALID_INPUT" and "PRIVATE" not in str(error.value)
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"artifactId": str(uuid4())},
        {"planId": str(uuid4())},
        {"chartArtifactId": str(uuid4())},
        {"planDigest": "f" * 64},
        {"chartArtifactDigest": "f" * 64},
        {"treeDigest": "PRIVATE"},
        {"bytes": 9007199254740993},
        {"bytes": "0"},
        {"bytes": "01"},
        {"bytes": "9223372036854775808"},
        {"fileCount": True},
        {"fileCount": 0},
        {"fileCount": 100001},
        {"fileCount": -1},
        {"executionState": "ready"},
        {"continuationAuthorized": True},
        {"continuationAuthorized": 0},
        {"arguments": ["PRIVATE"]},
        {"manifest": {"private": "PRIVATE"}},
        {"paths": ["PRIVATE"]},
    ],
)
def test_foreign_private_malformed_or_authorizing_tree_summaries_are_refused(change):
    sdk, _ = client(charts=chart_summary(), tree={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        read(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE" not in str(error.value)


def test_preparation_enforces_its_budget_and_read_rejects_preparation_controls():
    sdk, _ = peer()
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_tree(JOB, intent=INTENT, body={**body(), "maximumBytes": "1"})
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    with pytest.raises(ValueError):
        protocol.request(body(), job_id=JOB, read_only=True)


@pytest.mark.parametrize(
    "capability",
    [
        {},
        {"format": True, "prepare": True, "readOnly": True, "continuation": False},
        {"format": 1, "prepare": True, "readOnly": True, "continuation": True},
        {"format": 1, "prepare": True, "readOnly": False, "continuation": False},
    ],
)
def test_missing_or_unsafe_tree_capability_is_refused_before_source_or_tree_calls(capability):
    sdk, calls = peer(tree_capability=capability)
    with sdk, pytest.raises(NvkeError) as error:
        read(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(path == "/v1/identity" for _, path, _ in calls)


def test_wrong_reviewed_chart_digest_prevents_tree_rpc():
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_tree(JOB, intent=INTENT, body={**body(), "chartArtifactDigest": "f" * 64})
    assert error.value.code == "SOURCE_CHANGED" and tree_calls(calls) == []


@pytest.mark.parametrize(
    "change", [{"changed_after_tree": True}, {"changed_chart_after_tree": True}, {"replacement": True}]
)
def test_parent_publication_or_agent_changes_cannot_expose_a_tree_result(change):
    sdk, calls = peer(**change)
    with sdk, pytest.raises(NvkeError) as error:
        read(sdk)
    assert error.value.code in {"SOURCE_CHANGED", "OPERATOR_INVALID_RESPONSE"}
    assert all(method == "GET" for method, _, _ in calls)


def test_lost_preparation_reply_is_not_replayed_and_recovers_only_through_explicit_read():
    sdk, calls = peer(lost_tree_reply=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            sdk.prepare_install_plan_tree(JOB, intent=INTENT, body=body())
        assert error.value.ambiguous and not error.value.retryable and "PRIVATE" not in str(error.value)
        assert read(sdk) == summary()
    assert len([1 for method, _, _ in calls if method == "POST"]) == 1


@pytest.mark.parametrize(
    "error_code,expected",
    [
        ("INSTALL_PLAN_TREE_INCOMPLETE", "OPERATOR_TREE_INCOMPLETE"),
        ("EXECUTION_TREE_INTENT_CHANGED", "OPERATOR_TREE_INTENT_CONFLICT"),
        ("EXECUTION_TREE_CONTENT_CHANGED", "SOURCE_CHANGED"),
        ("EXECUTION_TREE_BUSY", "OPERATOR_BUSY"),
        ("PRIVATE", "OPERATOR_REQUEST_REFUSED"),
    ],
)
def test_incomplete_or_refused_reads_remain_explicit_without_automatic_preparation(error_code, expected):
    sdk, calls = client(charts=chart_summary(), tree={"errorCode": error_code}, tree_status=409)
    with sdk, pytest.raises(NvkeError) as error:
        read(sdk)
    assert error.value.code == expected and "PRIVATE" not in str(error.value)
    assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("intent", [None, "", "PRIVATE", "a" * 63])
def test_invalid_original_intent_never_contacts_agent(intent):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_tree(JOB, intent=intent, body=body())
    assert error.value.code == "INVALID_INPUT" and calls == []


def test_revoked_current_authority_after_post_prevents_result_or_replay():
    sdk, calls = peer()

    def revoked():
        if any(method == "POST" for method, _, _ in tree_calls(calls)):
            raise NvkeError("AUTHORIZATION_CHANGED", "Current authority revoked")

    sdk._checkpoint = revoked
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_tree(JOB, intent=INTENT, body=body())
    assert error.value.code == "AUTHORIZATION_CHANGED"
    assert len([1 for method, _, _ in tree_calls(calls) if method == "POST"]) == 1


def test_capability_drift_after_private_preparation_prevents_result():
    capability = {"format": 1, "prepare": True, "readOnly": True, "continuation": False}
    sdk, calls = peer(tree_capability=capability)

    def changed():
        if any(method == "POST" for method, _, _ in tree_calls(calls)):
            capability["prepare"] = False

    sdk._checkpoint = changed
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_tree(JOB, intent=INTENT, body=body())
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert len([1 for method, _, _ in tree_calls(calls) if method == "POST"]) == 1


@pytest.mark.parametrize(
    "tree_value,charts_value",
    [
        ({**summary(), "bytes": "1"}, chart_summary()),
        ({**summary(), "fileCount": 2}, {**chart_summary(), "chartCount": 3}),
    ],
)
def test_tree_summary_cannot_claim_fewer_files_or_bytes_than_its_chart_publication(tree_value, charts_value):
    sdk, calls = client(charts=charts_value, tree=tree_value)
    with sdk, pytest.raises(NvkeError) as error:
        read(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert all(method == "GET" for method, _, _ in calls)
