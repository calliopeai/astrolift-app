"""Convert a GHA PipelineDef to an Astrolift TOML string."""

from __future__ import annotations

from astrolift_ci_convert.common.toml_writer import write_toml
from astrolift_ci_convert.common.types import PipelineDef


def convert(pipeline: PipelineDef) -> str:
    """Serialize a parsed GHA PipelineDef as Astrolift TOML."""
    return write_toml(pipeline)
