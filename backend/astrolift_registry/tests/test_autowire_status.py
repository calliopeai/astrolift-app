"""Tests for the autowire status rollup on the app type (#1108).

``build_autowire_status`` is the DB-only read-path derivation the app detail
resolver uses. It reads the persisted ``autowire_state`` snapshot plus the
webhook columns and a live connection check — no host round-trip. These tests
pin the phantom derivation (the reported bug), the persisted-state path, and
the ``connected`` gate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.schema.types import build_autowire_status
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-status")


def _make_app(org, *, slug="st", **kw):
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug}")
    project = Project.objects.create(organization=org, team=team, name="P", slug=f"p-{slug}")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-{slug}",
        source_kind="github",
        source_repo="acme/api",
        **kw,
    )


def _app_conn(org):
    encrypted = encrypt_at_rest(b"fake-pem")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App: acme",
        account_login="acme",
        installation_id="987",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def test_phantom_derived_from_columns_when_never_run(org):
    """installed_at set + empty id + no autowire_state = the reported phantom.
    The rollup surfaces it so the banner prompts a repair."""
    app = _make_app(
        org,
        slug="phantom",
        source_webhook_id="",
        source_webhook_installed_at=timezone.now(),
    )

    status = build_autowire_status(app)

    assert status.webhook == "phantom"
    # Never-run app: the other steps read as missing.
    assert status.ci_workflow == "missing"
    assert status.secrets == "missing"
    assert status.checked_at is None


def test_real_hook_id_reads_ok(org):
    app = _make_app(
        org,
        slug="realhook",
        source_webhook_id="4242",
        source_webhook_installed_at=timezone.now(),
    )

    assert build_autowire_status(app).webhook == "ok"


def test_uses_persisted_state(org):
    app = _make_app(
        org,
        slug="persisted",
        autowire_state={
            "connected": True,
            "ci_workflow": "ok",
            "webhook": "ok",
            "secrets": "error",
            "checked_at": "2026-07-15T00:00:00+00:00",
            "errors": {"secrets": "needs the org GitHub App"},
        },
    )

    status = build_autowire_status(app)

    assert status.ci_workflow == "ok"
    assert status.webhook == "ok"
    assert status.secrets == "error"
    assert "secrets" in status.detail
    assert "GitHub App" in status.detail
    assert status.checked_at is not None


def test_persisted_webhook_verdict_wins_over_columns(org):
    """After an autowire run, a recorded ``ok`` webhook verdict is trusted even
    if the columns still look phantom (empty id from an app_delivers repo)."""
    app = _make_app(
        org,
        slug="verdict",
        source_webhook_id="",
        source_webhook_installed_at=timezone.now(),
        autowire_state={"connected": True, "webhook": "ok"},
    )

    assert build_autowire_status(app).webhook == "ok"


def test_connected_reflects_org_connection(org):
    app = _make_app(org, slug="conn")
    assert build_autowire_status(app).connected is False

    _app_conn(org)
    assert build_autowire_status(app).connected is True


def test_missing_when_fresh(org):
    app = _make_app(org, slug="fresh")
    status = build_autowire_status(app)
    assert status.ci_workflow == "missing"
    assert status.webhook == "missing"
    assert status.secrets == "missing"
    assert status.detail == ""


def test_detail_resolver_surfaces_autowire(permission_resolver, org):
    """The single-app detail resolver populates ``autowire`` end-to-end
    (resolver → app_to_type → field), where the list path leaves it null."""
    app = _make_app(
        org,
        slug="resolver",
        source_webhook_id="",
        source_webhook_installed_at=timezone.now(),
    )
    _app_conn(org)
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        row = RegistryQuery().astrolift_app(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            slug=app.slug,
        )

    assert row is not None
    assert row.autowire is not None
    assert row.autowire.connected is True
    # installed_at set + empty id + never-run = the phantom the banner flags.
    assert row.autowire.webhook == "phantom"
