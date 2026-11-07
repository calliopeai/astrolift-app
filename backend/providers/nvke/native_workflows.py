"""Pinned native Temporal vocabulary, separate from HTTP/datastore execution."""

import copy
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from nvke.client import NvkeError, _validate
from nvke.contract import _contract


def workflows() -> list[dict[str, Any]]:
    return copy.deepcopy(_contract()["native_workflows"])


def validate_input(name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    selected = next((row for row in workflows() if row["name"] == name), None)
    if selected is None:
        raise NvkeError("INVALID_INPUT", "Unknown NVKE native workflow")
    _validate(selected["input_schema"], parameters)
    scheduled = parameters.get("scheduledFor")
    if scheduled is not None:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", scheduled):
                raise ValueError
            parsed_time = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
            if parsed_time.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise NvkeError("INVALID_INPUT", "Use an RFC3339 native workflow schedule") from None
    for field in ("clusterID", "nodeID"):
        if field in parameters:
            value = parameters[field]
            try:
                parsed = UUID(value)
                if not parsed.int or str(parsed) != value:
                    raise ValueError
            except (TypeError, ValueError, AttributeError):
                raise NvkeError("INVALID_INPUT", "Use the exact nonzero native resource UUID") from None
    owner = parameters.get("rotationWorkflowID")
    if owner is not None:
        try:
            if len(owner.encode("utf-8")) > 1000:
                raise ValueError
        except (UnicodeError, ValueError):
            raise NvkeError("INVALID_INPUT", "Use a bounded native rotation owner identity") from None
    return copy.deepcopy(parameters)
