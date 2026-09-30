"""Opt-in data-migration prerequisites for identity tests."""

from importlib import import_module

import pytest
from django.apps import apps


@pytest.fixture
def system_role_catalog(db):
    """Transactional flushes remove seed rows without rerunning migrations."""
    migration = import_module("astrolift_identity.migrations.0034_resync_system_roles_stock_catalogue")
    migration.upsert_system_roles(apps, None)
