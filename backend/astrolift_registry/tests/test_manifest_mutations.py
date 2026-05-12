"""Tests for the manifest editor mutations (#277).

Covers update_manifest (stage), sync_manifest_from_repo (refresh),
push_manifest_to_repo (open PR). The PR-creation side talks to SCM
providers and has a TODO marker; tests cover the stage/buffer/anchor
state machine that's pure-DB."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    PushManifestToRepoInput,
    RegistryMutation,
    SyncManifestFromRepoInput,
    UpdateManifestInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold(*, manifest_raw: str = "", manifest_hash: str = ""):
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        manifest_raw=manifest_raw,
        manifest_hash=manifest_hash,
        last_synced_hash=manifest_hash,
    )
    return org, app


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


_VALID_TOML = """
astrolift_version = 1
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
"""


def test_update_manifest_stages_to_staged_buffer(permission_resolver):
    org, app = _scaffold(manifest_raw="prior", manifest_hash="abc")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest=_VALID_TOML,
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    # manifest_raw is the source-of-truth; staging holds the draft
    assert app.manifest_raw == "prior"
    assert app.manifest_raw_staged.strip() == _VALID_TOML.strip()


def test_update_manifest_with_match_clears_staging_buffer(
    permission_resolver,
):
    """If the user's edit converges back to the source-of-truth,
    the staging buffer is cleared so the UI reads as in_sync."""
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    app.manifest_raw_staged = "uncommitted"
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest=_VALID_TOML,
            ),
        )

    assert result.ok
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_update_manifest_rejects_invalid_toml(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest="this is = not valid [toml ",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "rawManifest"
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_update_manifest_empty_clears_staging(permission_resolver):
    """Empty input is valid + clears the staging buffer (= 'discard')."""
    org, app = _scaffold(manifest_raw=_VALID_TOML)
    app.manifest_raw_staged = "leftover-draft"
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(id=str(app.guid), raw_manifest=""),
        )

    assert result.ok
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_update_manifest_requires_permission():
    org, app = _scaffold()
    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest=_VALID_TOML,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_sync_from_repo_clears_staging(permission_resolver):
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    app.manifest_raw_staged = "draft-to-be-discarded"
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(),
            input=SyncManifestFromRepoInput(id=str(app.guid)),
        )

    assert result.ok
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_sync_from_repo_anchors_last_synced_hash(permission_resolver):
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(),
            input=SyncManifestFromRepoInput(id=str(app.guid)),
        )

    assert result.ok
    app.refresh_from_db()
    assert app.last_synced_hash == "abc"


def test_push_with_no_staged_returns_nothing_to_push(
    permission_resolver,
):
    org, app = _scaffold(manifest_raw=_VALID_TOML)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(id=str(app.guid)),
        )

    assert result.ok
    assert result.data.note == "nothing_to_push"


def test_push_with_staged_returns_branch_name(permission_resolver):
    org, app = _scaffold(manifest_raw="prior")
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(id=str(app.guid)),
        )

    assert result.ok
    # SCM wiring is TODO; mutation reports scm_pending so the UI
    # can render 'PR opening...' state without exploding.
    assert result.data.note == "scm_pending"
    assert result.data.branch_name == "astrolift/manifest-hello-app"


def test_push_with_explicit_branch_uses_it(permission_resolver):
    org, app = _scaffold(manifest_raw="prior")
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(
                id=str(app.guid),
                branch_name="my-feature-branch",
            ),
        )

    assert result.ok
    assert result.data.branch_name == "my-feature-branch"


def test_update_manifest_unknown_app_returns_not_found(
    permission_resolver,
):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id="00000000-0000-0000-0000-000000000000",
                raw_manifest=_VALID_TOML,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
