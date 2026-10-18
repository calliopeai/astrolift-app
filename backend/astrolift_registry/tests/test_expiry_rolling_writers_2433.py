"""Pre-expiry images can insert into the migrated schema during rollout/rollback."""

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from astrolift_identity.models import Organization
from astrolift_registry.models import RegisteredApp
from astrolift_registry.tests.test_temporary_apps_2433 import _env, _world  # noqa: F401

pytestmark = pytest.mark.django_db


def test_pre_expiry_organization_and_app_writers_keep_their_insert_contract():
    state = MigrationLoader(connection).project_state(
        [
            ("astrolift_registry", "0047_agent_run_brains"),
            ("astrolift_identity", "0050_first_administrator"),
        ]
    )
    OldOrganization = state.apps.get_model("astrolift_identity", "Organization")
    OldApp = state.apps.get_model("astrolift_registry", "RegisteredApp")
    assert "temporary_app_policy" not in {field.name for field in OldOrganization._meta.fields}
    assert "expiry_cleanup" not in {field.name for field in OldApp._meta.fields}
    old_org = OldOrganization.objects.create(name="Older writer", slug="older-expiry-writer")
    assert Organization.objects.get(pk=old_org.pk).temporary_app_policy == {}
    world = _world("rolling2433")
    old_app = OldApp.objects.create(
        organization_id=world.org.pk,
        team_id=world.medops.pk,
        project_id=world.medops_project.pk,
        name="Older writer app",
        slug="older-expiry-app",
    )
    app = RegisteredApp.objects.get(pk=old_app.pk)
    assert app.expires_at is None
    assert app.delete_data_on_expiry is False
    assert app.expiry_warnings_sent == []
    assert app.expiry_cleanup == []
