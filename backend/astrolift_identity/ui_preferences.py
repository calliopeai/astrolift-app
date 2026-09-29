"""A person's UI preferences, validated against the frontend registries (#2154).

The vocabularies are the client's (``frontend/lib/viz-prefs.ts``,
``frontend/lib/display-prefs.ts``, ``frontend/components/home/registry.ts``)
and are repeated here for the reason ``core/appearance.py`` repeats the
appearance axes: the values are persisted, so the column has to be protected
from anyone posting the mutation without a browser. A value the client adds
has to land here too.

An unknown value is refused with the allowed set named, never dropped: a
choice that silently does not save is the bug this store exists to end.

Storage is "unset or a value": an empty column means the person never chose,
and the read resolves it to the default below, so a default that changes
later reaches everyone who did not choose.
"""

from __future__ import annotations

from typing import Any

HOME_LAYOUTS = frozenset({"apps", "agents", "builder", "operator"})
FLEET_VIEWS = frozenset({"orbit", "heartbeat", "hive", "manifest", "isometric", "graph", "swarm", "list"})
WORKFLOW_VIEWS = frozenset({"transit", "isometric", "graph", "list"})
APP_VIEWS = frozenset({"auto", "isometric", "graph", "classic"})
MOTION_MODES = frozenset({"system", "full", "reduced"})
RESTRICTED_SETTINGS = frozenset({"show", "hide"})

DEFAULT_FLEET_VIEW = "orbit"
DEFAULT_WORKFLOW_VIEW = "transit"
DEFAULT_APP_VIEW = "auto"
DEFAULT_MOTION = "system"
DEFAULT_FLOW_PARTICLES = True
#: What an org shows people who have not chosen, until the org says otherwise.
DEFAULT_RESTRICTED_SETTINGS = "show"

#: Model column -> (allowed values, camelCase input name).
CHOICE_FIELDS: dict[str, tuple[frozenset[str], str]] = {
    "home_layout": (HOME_LAYOUTS, "homeLayout"),
    "fleet_view": (FLEET_VIEWS, "fleetView"),
    "workflow_view": (WORKFLOW_VIEWS, "workflowView"),
    "app_view": (APP_VIEWS, "appView"),
    "motion": (MOTION_MODES, "motion"),
    "restricted_settings": (RESTRICTED_SETTINGS, "restrictedSettings"),
}


class UiPreferenceError(ValueError):
    """A preference value outside the registry. ``field`` is the input name."""

    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def validate_choice(column: str, value: Any) -> str:
    """Return ``value`` if the registry for ``column`` holds it, else raise."""
    allowed, field = CHOICE_FIELDS[column]
    if not isinstance(value, str) or value not in allowed:
        raise UiPreferenceError(
            f"invalid {field} {value!r}; allowed: {', '.join(sorted(allowed))}",
            field=field,
        )
    return value


def validate_restricted_settings(value: Any, *, field: str) -> str:
    """The org default takes the same two values as the personal choice."""
    if not isinstance(value, str) or value not in RESTRICTED_SETTINGS:
        raise UiPreferenceError(
            f"invalid {field} {value!r}; allowed: {', '.join(sorted(RESTRICTED_SETTINGS))}",
            field=field,
        )
    return value


def org_restricted_default(org_id: int | None) -> str:
    """The active org's show-or-hide default; the platform default with no org."""
    if org_id is None:
        return DEFAULT_RESTRICTED_SETTINGS
    from astrolift_identity.models import Organization

    value = (
        Organization.objects.filter(pk=org_id).values_list("restricted_settings_default", flat=True).first()
    )
    return value or DEFAULT_RESTRICTED_SETTINGS
