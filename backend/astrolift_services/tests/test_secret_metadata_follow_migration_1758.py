"""Migration 0027: per-environment secret metadata rows written before 0026
always stored a scope, so each pinned its environment and an approved
app-wide narrowing never reached it (#1758). A row that matched its key's
app-wide scope now follows it; a row that differed stays an override."""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import AppSecretMetadata
from astrolift_services.secret_metadata_ops import current_secret_scope

pytestmark = pytest.mark.django_db

_MIGRATION = import_module(
    "astrolift_services.migrations.0027_appsecretmetadata_follow_matching_app_wide_scope"
)


def _app(slug: str) -> RegisteredApp:
    org = Organization.objects.create(name=slug, slug=slug)
    team = Team.objects.create(organization=org, name=slug, slug=slug)
    project = Project.objects.create(organization=org, team=team, name=slug, slug=slug)
    return RegisteredApp.objects.create(organization=org, team=team, project=project, name=slug, slug=slug)


def _row(app, key: str, environment_name: str, scope: str, **fields) -> AppSecretMetadata:
    return AppSecretMetadata.objects.create(
        registered_app=app, key=key, environment_name=environment_name, scope=scope, **fields
    )


def test_old_per_environment_rows_that_match_the_app_wide_scope_follow_it():
    app = _app("follow-app")
    other = _app("follow-other-app")
    app_wide = _row(app, "RESTRICTED", "", "production")
    rows = {
        "matches the app-wide row": _row(app, "RESTRICTED", "preview-a", "production"),
        "differs from the app-wide row": _row(app, "RESTRICTED", "preview-b", "all"),
        "matches the default": _row(app, "NO_APP_WIDE_ROW", "preview-a", "all"),
        "differs from the default": _row(app, "NO_APP_WIDE_ROW", "preview-b", "preview"),
        "soft-deleted": _row(app, "RESTRICTED", "preview-c", "production", deleted_at=timezone.now()),
        "another app's key, no app-wide row": _row(other, "RESTRICTED", "preview-a", "production"),
    }
    live = [row for row in rows.values() if row.deleted_at is None]
    in_force_before = [
        current_secret_scope(row.registered_app, row.key, row.environment_name) for row in live
    ]

    for _ in range(2):
        _MIGRATION.follow_matching_app_wide_scope(apps, None, batch_size=2)

        stored = {name: AppSecretMetadata.all_objects.get(pk=row.pk).scope for name, row in rows.items()}
        assert stored == {
            "matches the app-wide row": None,
            "differs from the app-wide row": "all",
            "matches the default": None,
            "differs from the default": "preview",
            "soft-deleted": "production",
            "another app's key, no app-wide row": "production",
        }
    assert AppSecretMetadata.objects.get(pk=app_wide.pk).scope == "production"
    assert [
        current_secret_scope(row.registered_app, row.key, row.environment_name) for row in live
    ] == in_force_before


def test_a_row_that_now_follows_takes_a_later_app_wide_narrowing():
    app = _app("follow-narrow-app")
    preview_row = _row(app, "K", "preview-a", "all")

    _MIGRATION.follow_matching_app_wide_scope(apps, None)
    _row(app, "K", "", "production")

    assert AppSecretMetadata.objects.get(pk=preview_row.pk).scope is None
    assert current_secret_scope(app, "K", "preview-a") == "production"
