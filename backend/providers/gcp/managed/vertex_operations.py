"""Family-local Vertex wire identities and bounded LRO reads (#2277)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class VertexOperationError(ValueError):
    pass


@dataclass
class VertexPlan:
    state: dict[str, Any]
    phase: str = ""
    method: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    pending: bool = False
    complete: bool = False
    message: str = ""


def resource_name(value: Any, *, parent: str, collection: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        re.escape(f"{parent}/{collection}/") + r"[A-Za-z0-9_-]{1,128}", value
    ):
        raise VertexOperationError("Vertex resource project, region or identity is unknown")
    return value


def operation_name(value: Any, *, parent: str) -> str:
    return resource_name(value, parent=parent, collection="operations")


def operation_receipt(future: Any, *, parent: str) -> str:
    operation = getattr(future, "operation", None)
    return operation_name(getattr(operation, "name", None), parent=parent)


def poll_operation(client: Any, name: str, *, parent: str, response_type: str) -> Any | None:
    """One RPC; never Operation.result(), background polling, or a cloud write."""
    from google.cloud import aiplatform_v1
    from google.protobuf.empty_pb2 import Empty

    name = operation_name(name, parent=parent)
    transport = getattr(client, "transport", None)
    operations = getattr(transport, "operations_client", None) or getattr(client, "operations_client", None)
    operations = operations or client
    operation = operations.get_operation(name=name, retry=None, timeout=10)
    if getattr(operation, "name", "") != name:
        raise VertexOperationError("Vertex operation response identity changed")
    if not operation.done:
        return None
    if operation.HasField("error"):
        raise VertexOperationError(f"Vertex operation failed (code {operation.error.code}); operator review required")
    if not operation.HasField("response"):
        raise VertexOperationError("Vertex completed operation has no verifiable result")
    result_type = Empty if response_type == "Empty" else getattr(aiplatform_v1, response_type)
    result = result_type() if response_type == "Empty" else result_type.pb(result_type())
    if not operation.response.Unpack(result):
        raise VertexOperationError("Vertex completed operation has an unexpected result type")
    return result if response_type == "Empty" else result_type(result)
