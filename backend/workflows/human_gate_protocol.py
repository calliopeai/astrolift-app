"""Pure validation shared by the gate API and deterministic worker update."""

from uuid import UUID

GATE_UPDATE = "decide_human_gate"
MAX_GATE_NOTE = 4096


def decision_update_id(execution_guid: str) -> str:
    return f"astrolift-human-gate-{UUID(execution_guid)}"


def validate_gate_request(payload: dict) -> None:
    if not isinstance(payload, dict) or set(payload) != {
        "execution_id",
        "execution_guid",
        "decision",
        "decided_by_user_id",
        "note",
    }:
        raise ValueError("Invalid human gate request")
    if payload["decision"] not in ("approved", "rejected"):
        raise ValueError("Decision must be approved or rejected")
    if not isinstance(payload["note"], str) or len(payload["note"]) > MAX_GATE_NOTE:
        raise ValueError("Invalid human gate note")
    if type(payload["decided_by_user_id"]) is not int or payload["decided_by_user_id"] <= 0:
        raise ValueError("An authenticated approver is required")
    if not isinstance(payload["execution_id"], str) or not payload["execution_id"].isdecimal():
        raise ValueError("Invalid stage execution")
    UUID(payload["execution_guid"])
