"""
Project-level pytest fixtures.

Tests run against a real Postgres (no DB mocks). When pytest-django
sees ``DJANGO_SETTINGS_MODULE`` (set in pytest.ini), it manages the
test database lifecycle for us.

Fixtures exposed here:

* ``tenant_context`` — yields a TenantContext snapshot and binds it to
  the request-scoped contextvar for the duration of the test.
* ``permission_resolver`` — installs a deterministic permission
  resolver so tests don't have to model the full RBAC chain to write
  a mutation test; revert on teardown.
* ``temporal_env`` / ``temporal_worker`` — Temporal time-skipping
  test environment.
* ``request_id`` — sets a known ULID for log assertions.

Per-app conftests (``core/tests/conftest.py``, etc.) layer on
domain-specific factories; this file only defines cross-cutting ones.
"""

from __future__ import annotations

import os

# pytest-django's pytest_load_initial_conftests checks for
# ``DJANGO_CONFIGURATION`` and tries to import ``configurations`` when
# it's set. The platform now uses plain Django (no class-based
# settings), so unset the variable before pytest-django sees it.
# Settings.py still reads it as a label via ``CONFIGURATION``, so we
# preserve the value on a sibling key in case anything else needs it.
_label = os.environ.pop("DJANGO_CONFIGURATION", None)
if _label is not None:
    os.environ.setdefault("ASTROLIFT_ENV_LABEL", _label)

# Force Django app-loading BEFORE any per-app conftest imports run.
# Without this the lifecycle / workflows / clusters conftests trip
# ``AppRegistryNotReady`` when they import model classes at module
# scope. pytest-django's own pytest_configure runs after the
# conftest-collection import phase has already touched models. (#396)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
import django  # noqa: E402

django.setup()

import pytest  # noqa: E402  (must follow the env-pop + django.setup above)

# ---------------------------------------------------------------------------
# TRUNCATE CASCADE for transaction=True tests.
#
# pytest-django's @pytest.mark.django_db(transaction=True) flushes the DB
# between tests via django.core.management.commands.flush, which on
# Postgres emits ``TRUNCATE <table>`` without CASCADE. Astrolift's schema
# has FK references between every business table (organization → app →
# environment → deployment, etc.), so the naked TRUNCATE fails with
# ``cannot truncate a table referenced in a foreign key constraint``.
#
# Django's own TransactionTestCase passes ``allow_cascade=True`` only
# when ``available_apps`` is set — pytest-django's marker doesn't set
# either, so we patch the Postgres operations' ``sql_flush`` to default
# ``allow_cascade=True``. This matches what real test runs need (full
# wipe + reload) and is scoped to the test session only.
# ---------------------------------------------------------------------------
from django.db.backends.base.operations import BaseDatabaseOperations  # noqa: E402
from django.db.backends.postgresql.operations import (  # noqa: E402
    DatabaseOperations as PostgresDatabaseOperations,
)

from core.permissions import register_permission_resolver  # noqa: E402
from core.request_context import generate_ulid, set_request_id  # noqa: E402
from core.tenancy import TenantContext  # noqa: E402
from core.tenancy import tenant_context as _tenant_ctx  # noqa: E402

_original_sql_flush_base = BaseDatabaseOperations.sql_flush
_original_sql_flush_pg = PostgresDatabaseOperations.sql_flush


def _sql_flush_with_cascade_base(self, style, tables, *, reset_sequences=False, allow_cascade=False):
    return _original_sql_flush_base(self, style, tables, reset_sequences=reset_sequences, allow_cascade=True)


def _sql_flush_with_cascade_pg(self, style, tables, *, reset_sequences=False, allow_cascade=False):
    return _original_sql_flush_pg(self, style, tables, reset_sequences=reset_sequences, allow_cascade=True)


BaseDatabaseOperations.sql_flush = _sql_flush_with_cascade_base
PostgresDatabaseOperations.sql_flush = _sql_flush_with_cascade_pg


# Re-export the reusable two-tenant fixture (#537). Importing it here
# makes ``two_tenants`` available to every test without per-file
# ``pytest_plugins`` wiring.
from core.tests.fixtures.tenants import two_tenants  # noqa: E402, F401


@pytest.fixture(autouse=True)
def _reset_abac_attributes():
    """Drop the ABAC request attributes (and their per-request policy cache)
    after every test. A test that drives middleware or a consumer directly
    never reaches process_response, so without this the next test in the
    worker would read the previous one's cached policies (#2157)."""
    yield
    from astrolift_identity.abac import clear_request_attributes

    clear_request_attributes()


@pytest.fixture(autouse=True)
def _reset_request_id():
    rid = generate_ulid()
    token = set_request_id(rid)
    yield rid
    from core.request_context import reset_request_id

    reset_request_id(token)


@pytest.fixture
def tenant_context():
    """Yields a TenantContext snapshot bound to the contextvar."""

    def _make(
        organization_id: int = 1,
        team_id: int | None = None,
        project_id: int | None = None,
        actor_user_id: int | None = None,
    ):
        return TenantContext(
            organization_id=organization_id,
            team_id=team_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
        )

    return _make


@pytest.fixture
def with_tenant(tenant_context):
    def _enter(**kwargs):
        return _tenant_ctx(tenant_context(**kwargs))

    return _enter


@pytest.fixture
def permission_resolver():
    """Installs a permission resolver controllable from the test."""

    grants: dict[tuple[str, str | None, int | None], bool] = {}

    def grant(permission, *, scope=None):
        key = (permission.value, scope.kind.value if scope else None, scope.id if scope else None)
        grants[key] = True

    def deny(permission, *, scope=None):
        key = (permission.value, scope.kind.value if scope else None, scope.id if scope else None)
        grants[key] = False

    def _resolver(_tenant, permission, scope):
        key = (permission.value, scope.kind.value if scope else None, scope.id if scope else None)
        if key in grants:
            return grants[key], "test grant" if grants[key] else "test deny"
        # An unscoped ``grant(perm)`` means "the caller holds this
        # permission", so it answers a scoped check too (#1717 threaded
        # ``scope=`` through resolvers these tests reach unscoped).
        # ``deny(perm, scope=...)`` still wins, because the exact key is
        # consulted first.
        unscoped = (permission.value, None, None)
        if scope is not None and unscoped in grants:
            return grants[unscoped], "test grant" if grants[unscoped] else "test deny"
        return False, "no grant in test"

    from core.permissions import (
        ALL_SCOPES,
        GrantedScopes,
        ScopeKind,
        get_granted_scopes_provider,
        get_permission_resolver,
        register_granted_scopes_provider,
    )

    def _scopes_provider(_tenant, permission):
        """Mirror the resolver's explicit grants for any-scope collection tests."""
        if grants.get((permission.value, None, None)) is True:
            return ALL_SCOPES
        ids = {ScopeKind.APP: set(), ScopeKind.PROJECT: set(), ScopeKind.TEAM: set()}
        for (value, kind, scope_id), allowed in grants.items():
            if value != permission.value or not allowed or kind is None:
                continue
            try:
                scope_kind = ScopeKind(kind)
            except ValueError:
                continue
            if scope_kind in ids and scope_id is not None:
                ids[scope_kind].add(scope_id)
        return GrantedScopes(
            org=False,
            team_ids=frozenset(ids[ScopeKind.TEAM]),
            project_ids=frozenset(ids[ScopeKind.PROJECT]),
            app_ids=frozenset(ids[ScopeKind.APP]),
        )

    previous = get_permission_resolver()
    # ``any_scope=True`` gates read the granted-scopes provider, not the
    # resolver. Point it back at this stub so one ``grant()`` still
    # controls both halves of the gate.
    previous_provider = get_granted_scopes_provider()
    register_permission_resolver(_resolver)
    register_granted_scopes_provider(_scopes_provider)
    yield type("Resolver", (), {"grant": staticmethod(grant), "deny": staticmethod(deny)})
    register_permission_resolver(previous)
    register_granted_scopes_provider(previous_provider)


@pytest.fixture
async def temporal_env():
    if os.getenv("ASTROLIFT_SKIP_TEMPORAL_TESTS"):
        pytest.skip("ASTROLIFT_SKIP_TEMPORAL_TESTS set")

    from core.testing.temporal import temporal_test_env

    async with temporal_test_env() as env:
        yield env
