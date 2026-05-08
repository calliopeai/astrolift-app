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

import pytest  # noqa: E402  (must follow the env-pop above)

from core.permissions import register_permission_resolver  # noqa: E402
from core.request_context import generate_ulid, set_request_id  # noqa: E402
from core.tenancy import TenantContext  # noqa: E402
from core.tenancy import tenant_context as _tenant_ctx  # noqa: E402


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
        return False, "no grant in test"

    from core.permissions import get_permission_resolver

    previous = get_permission_resolver()
    register_permission_resolver(_resolver)
    yield type("Resolver", (), {"grant": staticmethod(grant), "deny": staticmethod(deny)})
    register_permission_resolver(previous)


@pytest.fixture
async def temporal_env():
    if os.getenv("ASTROLIFT_SKIP_TEMPORAL_TESTS"):
        pytest.skip("ASTROLIFT_SKIP_TEMPORAL_TESTS set")

    from core.testing.temporal import temporal_test_env

    async with temporal_test_env() as env:
        yield env
