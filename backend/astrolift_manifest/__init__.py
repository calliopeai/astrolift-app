"""Astrolift manifest (``astrolift.toml``) parser + normalizer.

Two-step pipeline:

1. ``parse_raw(toml_text)`` — load the raw TOML into a typed
   ``RawManifest`` dataclass tree. Validates types and required keys.
2. ``normalize(raw, defaults=...)`` — apply defaults from the
   organization / team / project settings and produce a
   ``NormalizedManifest`` (the JSON the platform stores in
   ``RegisteredApp.manifest_normalized``).

The normalizer is the single place where org-level defaults (cluster
selection, retention, preview policy) merge with the manifest. The
output JSON is what every other subsystem reads.

See ``specs/05-manifest-schema.md``.
"""

from astrolift_manifest.brief import BriefError, load_brief
from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.skills import SkillError, load_skill
from astrolift_manifest.types import (
    BriefRef,
    LoadedBrief,
    LoadedSkill,
    NormalizedManifest,
    RawManifest,
    SkillRef,
)

__all__ = [
    "BriefError",
    "BriefRef",
    "LoadedBrief",
    "LoadedSkill",
    "ManifestError",
    "NormalizedManifest",
    "RawManifest",
    "SkillError",
    "SkillRef",
    "load_brief",
    "load_skill",
    "normalize",
    "parse_raw",
]
