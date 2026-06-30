"""Format registry for visual-flow importers (#984).

A thin keyed lookup (``"langflow"`` → :class:`LangflowImporter`, …) so the
GraphQL surface dispatches by a format token without knowing the adapters.
Adding a format is one registry entry; the create/preview path downstream is
untouched.
"""

from __future__ import annotations

from workflows.importers.base import FlowImporter, FlowImportError, FlowImportResult
from workflows.importers.flowise import FlowiseImporter
from workflows.importers.langflow import LangflowImporter

_REGISTRY: dict[str, FlowImporter] = {
    LangflowImporter.format: LangflowImporter(),
    FlowiseImporter.format: FlowiseImporter(),
}


def available_formats() -> list[str]:
    """Sorted list of registered format tokens."""
    return sorted(_REGISTRY)


def get_importer(fmt: str) -> FlowImporter:
    """Resolve a format token to its adapter. Raises :class:`FlowImportError`
    with the supported set on an unknown format."""
    importer = _REGISTRY.get(fmt)
    if importer is None:
        raise FlowImportError(f"unknown flow format {fmt!r}; supported: {', '.join(available_formats())}")
    return importer


def import_flow(fmt: str, payload) -> FlowImportResult:
    """Dispatch ``payload`` to the adapter for ``fmt`` and return its
    manifest + gap report."""
    return get_importer(fmt).import_flow(payload)
