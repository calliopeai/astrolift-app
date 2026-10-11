"""Application kinds: what an app is for, separate from how it runs (#2332).

Workload kinds (``deployment``, ``task``, ``agent``...) describe scheduling.
An app kind tells the platform and UI which purpose-built category an app
belongs to, so app-specific setup, navigation and defaults can key off it.
An omitted kind is a generic app. Register a new kind here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppKind:
    key: str
    label: str
    description: str


APP_KINDS: dict[str, AppKind] = {
    "mimwell": AppKind(
        key="mimwell",
        label="Mimwell",
        description="The brain-centered app that serves the Semantic Context Ontology Layer (SCOL).",
    ),
}

# Spec 05 once documented ``[app] kind = "web" | "worker"``; no parser read
# it. Those values stay accepted and mean a generic app, so no manifest that
# followed the old spec stops validating.
LEGACY_GENERIC_KINDS = frozenset({"web", "worker"})


def resolve_app_kind(value: object) -> str:
    """The registered app kind for a manifest value, ``""`` for generic.

    Raises ``ValueError`` naming the supported kinds for anything else.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("app.kind must be a string")
    kind = value.strip().lower()
    if not kind or kind in LEGACY_GENERIC_KINDS:
        return ""
    if kind not in APP_KINDS:
        supported = ", ".join(sorted(APP_KINDS))
        raise ValueError(
            f"unknown app kind {value!r}; supported kinds: {supported} (omit kind for a generic app)"
        )
    return kind
