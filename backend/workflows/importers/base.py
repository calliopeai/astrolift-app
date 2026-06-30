"""Generalized visual-flow importer abstraction (#984).

Popular visual agent/workflow builders (Langflow, Flowise, …) each export a
node/edge graph in their own JSON shape. This package maps those exports onto
Astrolift's native workflow representation — the #973 structured manifest
(:class:`~workflows.manifest.ParsedWorkflowManifest` =
:class:`~workflows.manifest.WorkflowDefSpec` + ordered
:class:`~workflows.manifest.WorkflowStageSpec`) — so the downstream
preview/create path is shared verbatim with the native TOML manifest, never
reinvented.

A :class:`FlowImporter` is a per-format adapter exposing two pure methods:

* :meth:`FlowImporter.gap_report` — structured :class:`ImportGap` list naming
  every source construct that did *not* translate cleanly (a routing node, a
  memory node, a cycle …). Unmappable constructs surface as **warnings, not
  silent drops**, so the operator sees what was lost.
* :meth:`FlowImporter.to_manifest` — the parsed export as a
  ``ParsedWorkflowManifest``.

Both share one private pass (:meth:`FlowImporter._analyze`) so a payload is
mapped once. :meth:`FlowImporter.import_flow` returns the bundle of both as a
:class:`FlowImportResult`.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any

from workflows.manifest import ParsedWorkflowManifest


class FlowImportError(ValueError):
    """A flow payload could not be mapped at all (malformed / not the claimed
    format). Distinct from an :class:`ImportGap`, which is a *partial* loss on
    an otherwise-importable flow."""


@dataclasses.dataclass(frozen=True)
class ImportGap:
    """One source construct that did not translate cleanly.

    ``code`` is a stable machine token (e.g. ``"unmapped_node"``,
    ``"routing_unsupported"``, ``"graph_cycle"``); ``message`` is the
    operator-facing explanation. ``node_id`` / ``node_type`` locate the
    offending node when the gap is node-scoped. ``severity`` is ``"warning"``
    (something was dropped/approximated) or ``"info"`` (a benign, expected
    non-stage construct such as an input/output node).
    """

    code: str
    message: str
    node_id: str | None = None
    node_type: str | None = None
    severity: str = "warning"


@dataclasses.dataclass
class FlowImportResult:
    """A mapped flow: the structured manifest plus the gap report."""

    manifest: ParsedWorkflowManifest
    gaps: list[ImportGap]


class FlowImporter(ABC):
    """Per-format adapter base. Subclasses set :attr:`format` and implement
    :meth:`_analyze`; :meth:`gap_report` / :meth:`to_manifest` /
    :meth:`import_flow` are derived so a payload is mapped exactly once."""

    #: The format token this adapter is registered under (``"langflow"``, …).
    format: str = ""

    @abstractmethod
    def _analyze(self, payload: dict[str, Any]) -> FlowImportResult:
        """Map a parsed export payload → manifest + gaps. The single pass both
        public methods delegate to. Raise :class:`FlowImportError` if the
        payload is not a recognizable flow of this format."""
        raise NotImplementedError

    def import_flow(self, payload: dict[str, Any]) -> FlowImportResult:
        """Full mapping — manifest + gaps."""
        return self._analyze(_as_dict(payload))

    def to_manifest(self, payload: dict[str, Any]) -> ParsedWorkflowManifest:
        """The parsed export as a ``ParsedWorkflowManifest`` (no persistence)."""
        return self._analyze(_as_dict(payload)).manifest

    def gap_report(self, payload: dict[str, Any]) -> list[ImportGap]:
        """Structured list of constructs that did not translate cleanly."""
        return self._analyze(_as_dict(payload)).gaps


def _as_dict(payload: Any) -> dict[str, Any]:
    """Accept either a parsed dict (the GraphQL ``JSON`` scalar) or a raw JSON
    string (a pasted export). Normalize to a dict; reject anything else."""
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FlowImportError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FlowImportError("flow payload must be a JSON object")
    return payload
