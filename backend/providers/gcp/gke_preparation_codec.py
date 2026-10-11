"""Strict metadata-only preparation ledger codec; no clients or credential discovery."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from typing import TYPE_CHECKING, Any, cast

from gcp.gke_identity_preparation import (
    GKEIdentityPreparation,
    GKEPreparationLedger,
    PreparationIntent,
    PreparationPhase,
    PreparedObject,
)

if TYPE_CHECKING:
    from gcp.gke_identity_observation import GKEObservationContext


def preparation_ledger_payload(ledger: GKEPreparationLedger) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(json.dumps({"schema_version": 1, **asdict(ledger)})))


def preparation_ledger_from_payload(payload: dict[str, Any], *, context: GKEObservationContext) -> GKEPreparationLedger:
    try:
        if (
            type(payload) is not dict
            or set(payload) != {"schema_version", "target_sha256", "objects", "pending"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or len(json.dumps(payload, allow_nan=False).encode()) > 2 * 1024 * 1024
        ):
            raise ValueError
        parsed = {}
        for key, model in (("objects", PreparedObject), ("pending", PreparationIntent)):
            rows = payload[key]
            if not isinstance(rows, (list, tuple)) or len(rows) > 128:
                raise ValueError
            result = []
            for row in rows:
                if type(row) is not dict or set(row) != {f.name for f in fields(model)}:
                    raise ValueError
                if any(type(value) is not str for value in row.values()):
                    raise ValueError
                row = dict(row)
                if key == "pending":
                    row["phase"] = PreparationPhase(row["phase"])
                result.append(model(**row))
            parsed[key] = tuple(result)
        ledger = GKEPreparationLedger(
            payload["target_sha256"],
            cast("tuple[PreparedObject, ...]", parsed["objects"]),
            cast("tuple[PreparationIntent, ...]", parsed["pending"]),
        )
        with GKEIdentityPreparation(context) as validator:
            validator._validate(ledger)
        return ledger
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("INVALID_PREPARATION_LEDGER") from None
