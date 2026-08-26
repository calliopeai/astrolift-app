"""The wayfinding assistant's index and entitlement filter (#1101).

The epic's own claim is that the entitlement filter is what makes this
trustworthy rather than a generic doc search. So that filter is what these
tests are about; the model call itself is a thin wrapper over the pattern
`skill_ai_assist.py` already established.

Two properties carry the risk:

* **Pointing someone at a screen they cannot open is worse than saying
  nothing**, because they will believe the tool over the 403.
* **A redirect is not a destination.** Naming one sends someone somewhere
  the answer did not say, and the dictionary already records where it
  lands.
"""

from __future__ import annotations

import pytest

from astrolift_agents.services.wayfinding import (
    _SEGMENT_MODULE,
    Route,
    index_for_prompt,
    load_routes,
    module_for,
    visible_routes,
)

ALL_MODULES = {"apps": True, "agents": True, "workflows": True, "admin": True}


def _route(path, *, kind="page", nav_home="nav", notes=""):
    return Route(path=path, kind=kind, nav_home=nav_home, notes=notes, module=module_for(path))


# ---- the dictionary --------------------------------------------------


def test_the_route_dictionary_parses():
    """Grounding premise. If this returns nothing the assistant has nothing
    to answer from, and would say "no such screen" to every question."""
    routes = load_routes()

    assert len(routes) > 100
    assert all(r.path.startswith("/") for r in routes)


def test_the_packaged_dictionary_matches_the_frontend():
    """The gate that keeps the copy honest.

    The backend image contains `backend/` only, so reading
    `frontend/ROUTES.md` at runtime resolves to nothing in every deployed
    environment while working perfectly on a developer's checkout -- the
    assistant would answer "I can't see any screens" in production and be
    flawless locally. So the dictionary is packaged, and this asserts the
    copy is current wherever the source is present.

    Skipped rather than failed when the frontend is absent: this same suite
    runs inside the backend container, where its absence is the normal and
    correct state.
    """
    from astrolift_agents.services.wayfinding import FRONTEND_ROUTES_MD, ROUTES_MD

    if not FRONTEND_ROUTES_MD.exists():
        pytest.skip("frontend package not present; nothing to compare against")

    assert ROUTES_MD.read_text() == FRONTEND_ROUTES_MD.read_text(), (
        "backend/astrolift_agents/data/routes.md is stale. Refresh it with:\n"
        "  cp frontend/ROUTES.md backend/astrolift_agents/data/routes.md"
    )


def test_a_missing_dictionary_is_empty_not_an_exception(monkeypatch):
    """The backend is deployed without the frontend package in some images,
    and a help bubble that 500s is worse than one reporting nothing."""
    import pathlib

    import astrolift_agents.services.wayfinding as mod

    load_routes.cache_clear()
    monkeypatch.setattr(mod, "ROUTES_MD", pathlib.Path("/nonexistent/ROUTES.md"))
    try:
        assert load_routes() == ()
    finally:
        load_routes.cache_clear()


def test_known_routes_are_present():
    paths = {r.path for r in load_routes()}

    assert "/administration/members" in paths
    assert "/providers" in paths


def test_the_deleted_cost_console_is_absent():
    """The epic's own worked example deep-links to `/administration/cost`,
    removed in `7134d125`. The dictionary tracked the deletion, which is
    the drift protection working -- and the reason grounding in a
    CI-regenerated file is safe where a hand-written index would not be.
    """
    assert not any(r.path.startswith("/administration/cost") for r in load_routes())


# ---- the entitlement filter -----------------------------------------


def test_a_module_the_viewer_cannot_view_is_excluded():
    """The property the epic says makes this trustworthy."""
    visible = {r.path for r in visible_routes({**ALL_MODULES, "admin": False})}

    assert not any(p.startswith("/administration") for p in visible)
    assert any(p.startswith("/apps") for p in visible)


def test_an_absent_module_key_is_treated_as_no_access():
    """An incomplete manifest must not widen what the assistant offers."""
    visible = {r.path for r in visible_routes({"apps": True})}

    assert not any(p.startswith("/agents") for p in visible)


def test_an_empty_manifest_yields_only_unscoped_routes():
    visible = visible_routes({})

    assert all(r.module is None for r in visible)


def test_routes_with_no_module_are_always_visible():
    """Dashboard, documentation and account pages carry no entitlement,
    matching the frontend's own `PaletteEntry.module` convention."""
    visible = {r.path for r in visible_routes({})}

    assert any(p.startswith("/documentation") for p in visible)


def test_redirects_are_never_offered():
    """A redirect is a thin alias. Naming one sends someone somewhere the
    answer did not say."""
    assert all(r.is_page for r in visible_routes(ALL_MODULES))
    assert any(not r.is_page for r in load_routes()), "the fixture would be vacuous otherwise"


@pytest.mark.parametrize(
    "path,module",
    [
        ("/apps/hello", "apps"),
        ("/agents", "agents"),
        ("/workflows/x", "workflows"),
        ("/administration/audit", "admin"),
        ("/clusters", "admin"),
        ("/documentation/getting-started", None),
        ("/", None),
    ],
)
def test_module_mapping(path, module):
    assert module_for(path) == module


# ---- the prompt block ------------------------------------------------


def test_truncation_is_reported_not_silent():
    """An assistant that has seen 200 of 400 routes and does not say so
    answers "there is no such screen" with unearned confidence."""
    routes = tuple(_route(f"/apps/{i}") for i in range(50))

    block = index_for_prompt(routes, limit=10)

    assert "40 more routes not listed" in block


def test_no_truncation_note_when_everything_fits():
    block = index_for_prompt(tuple(_route(f"/apps/{i}") for i in range(3)), limit=10)

    assert "not listed" not in block


def test_a_route_with_no_nav_entry_says_so():
    """So the person is not left hunting the sidebar for something only
    reachable by direct link."""
    block = index_for_prompt((_route("/apps/x", nav_home=""),))

    assert "no nav entry" in block


def test_every_mapped_module_is_a_real_entitlement_key():
    """The map's values must be keys `module_entitlements` actually emits.

    Nothing else would notice a rename. Every filter test above hands
    `visible_routes` a dictionary written by hand, so they agree with the
    map rather than with the producer; if `core.permissions` renamed
    `admin`, they would all still pass while the assistant silently stopped
    offering a single administration screen to anyone. Fail-closed and
    invisible is the failure mode this whole feature is meant not to have.
    """
    from core.permissions import module_entitlements

    real = {e.key for e in module_entitlements([], is_superuser=True)}
    mapped = set(_SEGMENT_MODULE.values())

    assert mapped <= real, (
        f"these segments map to module keys that no longer exist: {sorted(mapped - real)}. "
        f"module_entitlements emits {sorted(real)}. Routes under those segments are now "
        "invisible to every viewer, silently."
    )
