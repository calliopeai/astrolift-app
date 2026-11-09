from __future__ import annotations

from uuid import uuid4

import pytest

from nvke import _operator_install_plan_charts as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DIGEST, INTENT, JOB, PLAN, client

ARTIFACT = str(uuid4())


def body():
    return {
        "format": 1,
        "artifactId": ARTIFACT,
        "planId": PLAN,
        "planDigest": DIGEST,
        "maximumBytes": "9007199254740993",
        "maximumExpandedBytes": "9223372036854775807",
    }


def summary():
    return {
        "format": 1,
        "artifactId": ARTIFACT,
        "planId": PLAN,
        "planDigest": DIGEST,
        "artifactDigest": "c" * 64,
        "chartCount": 1,
        "bytes": "9007199254740993",
        "expandedBytes": "9223372036854775807",
        "executionState": "requires-rendering-images-and-live-verification",
        "continuationAuthorized": False,
    }


def read(sdk):
    return sdk.install_plan_charts(JOB, intent=INTENT, plan_id=PLAN, plan_digest=DIGEST, artifact_id=ARTIFACT)


def test_prepare_and_read_preserve_large_byte_strings_and_never_dispatch_native_work():
    sdk, calls = client(charts=summary())
    with sdk:
        assert sdk.prepare_install_plan_charts(JOB, intent=INTENT, body=body()) == summary()
        assert read(sdk) == summary()
    assert [(method, path) for method, path, _ in calls if method != "GET"] == [
        ("POST", f"/v1/jobs/{JOB}/install-plan-charts")
    ]
    query = next(params for method, path, params in calls if method == "GET" and path.endswith("/install-plan-charts"))
    assert query == {"artifactId": ARTIFACT, "planId": PLAN, "planDigest": DIGEST}


@pytest.mark.parametrize(
    "update",
    [
        {"format": True},
        {"artifactId": JOB},
        {"artifactId": PLAN},
        {"artifactId": "../outside"},
        {"planId": JOB},
        {"planDigest": "PRIVATE"},
        {"maximumBytes": 100},
        {"maximumBytes": True},
        {"maximumBytes": "0"},
        {"maximumExpandedBytes": "9223372036854775808"},
        {"maximumExpandedBytes": "01"},
        {"timeoutSeconds": 0},
        {"timeoutSeconds": True},
        {"timeoutSeconds": 3601},
        {"allowPlainHttp": "true"},
        {"executable": "PRIVATE"},
    ],
)
def test_invalid_reviewed_request_cannot_contact_agent(update):
    sdk, calls = client(charts=summary())
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_charts(JOB, intent=INTENT, body={**body(), **update})
    assert error.value.code == "INVALID_INPUT" and "PRIVATE" not in str(error.value)
    assert calls == []


@pytest.mark.parametrize(
    "update",
    [
        {"format": True},
        {"artifactId": str(uuid4())},
        {"planId": str(uuid4())},
        {"planDigest": "f" * 64},
        {"artifactDigest": "PRIVATE"},
        {"chartCount": True},
        {"chartCount": 4},
        {"chartCount": -1},
        {"bytes": 9007199254740993},
        {"bytes": "01"},
        {"bytes": "0"},
        {"expandedBytes": "9223372036854775808"},
        {"continuationAuthorized": True},
        {"continuationAuthorized": 0},
        {"executionState": "ready"},
        {"repository": "PRIVATE"},
        {"privatePayload": "PRIVATE"},
    ],
)
def test_malformed_or_authorizing_summary_is_refused_without_private_diagnostics(update):
    sdk, _ = client(charts={**summary(), **update})
    with sdk, pytest.raises(NvkeError) as error:
        read(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE" not in str(error.value)


def test_over_budget_summary_cannot_be_accepted_after_preparation():
    sdk, _ = client(charts=summary())
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_charts(JOB, intent=INTENT, body={**body(), "maximumBytes": "1"})
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


@pytest.mark.parametrize(
    "capability",
    [
        {},
        {"format": True, "prepare": True, "readOnly": True, "continuation": False},
        {"format": 1, "prepare": True, "readOnly": True, "continuation": True},
    ],
)
def test_missing_or_unsafe_capability_refused_before_chart_rpc(capability):
    sdk, calls = client(charts=summary(), chart_capability=capability)
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_charts(JOB, intent=INTENT, body=body())
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(path == "/v1/identity" for _, path, _ in calls)


def test_changed_parent_plan_after_response_is_refused():
    sdk, _ = client(charts=summary(), changed_after_chart=True)
    with sdk, pytest.raises(NvkeError) as error:
        read(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


def test_lost_preparation_reply_is_ambiguous_and_recovered_only_by_explicit_read():
    sdk, calls = client(charts=summary(), lost_chart_reply=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            sdk.prepare_install_plan_charts(JOB, intent=INTENT, body=body())
        assert error.value.ambiguous and not error.value.retryable
        assert "PRIVATE" not in str(error.value)
        assert read(sdk) == summary()
    assert len([1 for method, _, _ in calls if method == "POST"]) == 1


def test_empty_chart_stage_is_explicit_and_read_cannot_smuggle_download_controls():
    value = {**summary(), "chartCount": 0, "bytes": "0", "expandedBytes": "0"}
    assert protocol.summary(value, body=body(), item_count=3) == value
    with pytest.raises(ValueError):
        protocol.request(body(), job_id=JOB, read_only=True)


def test_authority_checkpoint_after_preparation_cannot_expose_result_or_replay():
    sdk, calls = client(charts=summary())

    def revoked():
        if any(method == "POST" for method, _, _ in calls):
            raise NvkeError("AUTHORIZATION_CHANGED", "Current authority revoked")

    sdk._checkpoint = revoked
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_charts(JOB, intent=INTENT, body=body())
    assert error.value.code == "AUTHORIZATION_CHANGED"
    assert len([1 for method, _, _ in calls if method == "POST"]) == 1


@pytest.mark.parametrize("intent", [None, "PRIVATE", "", "f" * 63])
def test_invalid_original_intent_cannot_contact_agent(intent):
    sdk, calls = client(charts=summary())
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_charts(JOB, intent=intent, body=body())
    assert error.value.code == "INVALID_INPUT" and calls == []


def test_capability_change_after_preparation_cannot_expose_result():
    capability = {"format": 1, "prepare": True, "readOnly": True, "continuation": False}
    sdk, calls = client(charts=summary(), chart_capability=capability)

    def changed():
        if any(method == "POST" for method, _, _ in calls):
            capability["prepare"] = False

    sdk._checkpoint = changed
    with sdk, pytest.raises(NvkeError) as error:
        sdk.prepare_install_plan_charts(JOB, intent=INTENT, body=body())
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert len([1 for method, _, _ in calls if method == "POST"]) == 1
