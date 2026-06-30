"""Generalized visual-flow importer framework (#984/#985/#986).

Public surface: a :func:`~workflows.importers.registry.import_flow` keyed by
format token, the :class:`~workflows.importers.base.FlowImporter` protocol,
and the structured :class:`~workflows.importers.base.ImportGap` /
:class:`~workflows.importers.base.FlowImportResult` types. Adapters map a
builder's node/edge export onto the #973 ``ParsedWorkflowManifest`` so the
preview/create path is shared with the native TOML manifest.
"""

from workflows.importers.base import (
    FlowImporter,
    FlowImportError,
    FlowImportResult,
    ImportGap,
)
from workflows.importers.registry import (
    available_formats,
    get_importer,
    import_flow,
)

__all__ = [
    "FlowImporter",
    "FlowImportError",
    "FlowImportResult",
    "ImportGap",
    "available_formats",
    "get_importer",
    "import_flow",
]
