"""Versioned NVKE operation and resource catalog, independent of Django."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

Surface = Literal["controller", "tenant", "operator"]
CONTRACT_PATH = Path(__file__).parent / "upstream" / "contract.json"


@lru_cache(maxsize=1)
def _contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text())  # type: ignore[no-any-return]


def upstream_revision() -> str:
    return str(_contract()["revision"])


@dataclass(frozen=True)
class Operation:
    id: str
    surface: Surface
    method: str
    path: str
    resource: str = ""

    @property
    def mutates(self) -> bool:
        return self.method not in {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Resource:
    kind: str
    plural: str
    group: str
    version: str
    namespaced: bool


def operations() -> tuple[Operation, ...]:
    return tuple(Operation(**item) for item in _contract()["operations"])


def operation(operation_id: str) -> Operation:
    for item in operations():
        if item.id == operation_id:
            return item
    raise ValueError("unknown NVKE operation")


def resources() -> tuple[Resource, ...]:
    return tuple(
        Resource(
            kind=crd["spec"]["names"]["kind"],
            plural=crd["spec"]["names"]["plural"],
            group=crd["spec"]["group"],
            version=version["name"],
            namespaced=crd["spec"]["scope"] == "Namespaced",
        )
        for crd in _contract()["custom_resources"]
        for version in crd["spec"]["versions"]
        if version["served"]
    )


def resource_schema(kind: str) -> dict[str, Any]:
    for crd in _contract()["custom_resources"]:
        if crd["spec"]["names"]["kind"] == kind:
            for version in crd["spec"]["versions"]:
                if version["storage"]:
                    return version["schema"]["openAPIV3Schema"]  # type: ignore[no-any-return]
    raise ValueError("unknown NVKE resource")


def controller_spec() -> dict[str, Any]:
    # Callers receive an independent document: schema conversion must not mutate
    # the baseline subsequently used by another request or by a drift check.
    return json.loads(json.dumps(_contract()["openapi"]))  # type: ignore[no-any-return]
