"""The database side of the managed-service match preview (#2606).

Uses the persist fixtures, so the rows are the ones a real reconcile writes.
The preview must read them and write nothing.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.managed_service_match import CREATE, RECONFIGURE, plan_digest
from astrolift_manifest.managed_service_match_db import build_match_plan
from astrolift_manifest.persist import persist_manifest
from astrolift_manifest.tests.test_persist import (
    _PG_MANIFEST,
    _add_environment,
    _catalog,
    _normalize,
    _scaffold,
)
from astrolift_services.models import ManagedService

pytestmark = pytest.mark.django_db


def _with_manifest(app, toml: str) -> None:
    app.manifest_raw = toml
    app.save(update_fields=["manifest_raw"])


def test_a_fresh_manifest_previews_a_create_and_writes_nothing(monkeypatch):
    _catalog(monkeypatch)
    app = _scaffold()
    _add_environment(app)
    _with_manifest(app, _PG_MANIFEST)

    declared, changes = build_match_plan(app)

    assert [(c.action, c.kind, c.name) for c in changes] == [(CREATE, "postgres", "records")]
    assert len(declared) == 1
    assert not ManagedService.objects.exists()
    assert plan_digest(declared, changes)


def test_an_active_service_that_matches_the_manifest_plans_nothing(
    monkeypatch, django_capture_on_commit_callbacks
):
    _catalog(monkeypatch)
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **k: None)
    app = _scaffold()
    _add_environment(app)
    _with_manifest(app, _PG_MANIFEST)
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)
    ManagedService.objects.filter(registered_app=app).update(
        status=ManagedService.Status.ACTIVE, backend_ref="postgres/records"
    )

    _declared, changes = build_match_plan(app)

    assert changes == ()


def test_a_changed_declaration_previews_a_reconfigure_without_applying_it(
    monkeypatch, django_capture_on_commit_callbacks
):
    _catalog(monkeypatch)
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **k: None)
    app = _scaffold()
    _add_environment(app)
    _with_manifest(app, _PG_MANIFEST)
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)
    ManagedService.objects.filter(registered_app=app).update(
        status=ManagedService.Status.ACTIVE, backend_ref="postgres/records"
    )
    changed = _PG_MANIFEST.replace('name = "records"', 'name = "records"\nsize = "medium"')
    _with_manifest(app, changed)
    before = ManagedService.objects.get(registered_app=app).config

    _declared, changes = build_match_plan(app)

    assert [(c.action, c.name) for c in changes] == [(RECONFIGURE, "records")]
    assert ManagedService.objects.get(registered_app=app).config == before
