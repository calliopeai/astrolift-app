from uuid import uuid4

import pytest

from nvke.client import NvkeError
from nvke.native_workflows import validate_input, workflows
from nvke.operations import actions


def test_operator_contract_is_distinct_from_the_native_http_catalog():
    assert {row["name"] for row in workflows()} == {
        "RenewK8sCertsV2",
        "RotateEtcdEncryptionKey",
        "RetireLostEtcdRotationNode",
    }
    assert len(actions()) == 88
    values = workflows()
    values[0]["input_schema"]["properties"].clear()
    assert workflows()[0]["input_schema"]["properties"]


@pytest.mark.parametrize(
    "name,extra",
    [
        ("RenewK8sCertsV2", {"thresholdDays": 120, "force": True}),
        ("RenewK8sCertsV2", {"thresholdDays": 1, "force": False, "scheduledFor": "2026-11-07T12:00:00Z"}),
        ("RenewK8sCertsV2", {"thresholdDays": 30, "force": True, "scheduledFor": None}),
        ("RotateEtcdEncryptionKey", {}),
        ("RetireLostEtcdRotationNode", {"nodeID": str(uuid4()), "rotationWorkflowID": "operator-更新-rotation"}),
    ],
)
def test_exact_native_inputs_preserve_operator_values(name, extra):
    value = {"clusterID": str(uuid4()), **extra}
    assert validate_input(name, value) == value


@pytest.mark.parametrize(
    "name,extra",
    [
        ("RenewK8sCertsV2", {"thresholdDays": 0}),
        ("RenewK8sCertsV2", {"thresholdDays": True}),
        ("RenewK8sCertsV2", {"thresholdDays": 30, "force": True, "scheduledFor": "2026-11-07T12:00:00Z"}),
        ("RenewK8sCertsV2", {"thresholdDays": 30, "scheduledFor": "not-a-time"}),
        ("RenewK8sCertsV2", {"thresholdDays": 9223372036854775808}),
        ("RotateEtcdEncryptionKey", {"privateKey": "PRIVATE_VALUE"}),
        ("RotateEtcdEncryptionKey", {"clusterID": "00000000-0000-0000-0000-000000000000"}),
        ("RetireLostEtcdRotationNode", {"nodeID": str(uuid4()), "rotationWorkflowID": "更新" * 400}),
        ("RetireLostEtcdRotationNode", {"nodeID": str(uuid4()), "rotationWorkflowID": ""}),
        ("UnknownWorkflow", {}),
    ],
)
def test_unknown_or_invalid_native_work_is_refused_without_echoing_inputs(name, extra):
    with pytest.raises(NvkeError) as error:
        validate_input(name, {"clusterID": str(uuid4()), **extra})
    assert error.value.code == "INVALID_INPUT"
    assert "PRIVATE" not in str(error.value)
