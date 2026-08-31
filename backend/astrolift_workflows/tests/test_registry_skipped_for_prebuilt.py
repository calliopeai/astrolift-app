"""Onboarding must not create a registry repo for a pre-built-image app (#1682).

``provision_registry_repo`` called ``ensure_repo`` for every app being
onboarded, without ever asking whether the app would push an image. The
platform already knew better -- ``_app_builds_an_image`` in the schema layer
documented exactly this rule -- but its only caller was a UI callout, so the
activity that actually creates the repository ignored it.

Two consequences, and the second is the one that bites: a repository is created
per app that will never receive an image, and on an install whose task role
cannot write to the registry the onboard *wedges* on a repo it was never going
to use. That closed off the one path which should work without registry
access at all -- bring-your-own-image, and air-gapped.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.app_lifecycle import _provision_registry_repo_sync

pytestmark = pytest.mark.django_db


def _app(suffix: str, **overrides) -> RegisteredApp:
    org = Organization.objects.create(name=f"Acme {suffix}", slug=f"acme-prebuilt-{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-prebuilt-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-prebuilt-{suffix}")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"eks-prebuilt-{suffix}",
        name="EKS",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        endpoint="https://eks.example.com",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Shop",
        slug=f"shop-prebuilt-{suffix}",
        provisioning_status="provisioning",
        **overrides,
    )
    AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return app


def _explode(*args, **kwargs):
    raise AssertionError("the registry driver must not be constructed for a pre-built-image app")


# ---- the property -------------------------------------------------


@pytest.mark.parametrize(
    ("build_mode", "source_kind", "expected"),
    [
        ("ci_pushed", "github", True),
        ("platform_build", "github", True),
        ("none", "github", False),
        ("ci_pushed", "direct_upload", False),
        ("none", "direct_upload", False),
    ],
)
def test_builds_an_image_covers_every_combination(build_mode, source_kind, expected):
    app = RegisteredApp(build_mode=build_mode, source_kind=source_kind)

    assert app.builds_an_image is expected


# ---- the activity -------------------------------------------------


def test_registry_is_skipped_when_the_image_is_pre_built(monkeypatch):
    """``build_mode = none`` means the operator supplies a published tag and
    nothing builds, so there is nothing to push and no repo to push it to."""
    app = _app("none", build_mode="none", source_kind="github")
    monkeypatch.setattr("core.app_deploy.driver_for_capability", _explode)

    assert _provision_registry_repo_sync(app.pk) == ""

    app.refresh_from_db()
    # The documented steady state for these apps -- build_reprovision_state
    # already suppresses the missing-registry callout on the strength of it.
    assert app.registry_repo_uri == ""


def test_registry_is_skipped_for_a_direct_upload(monkeypatch):
    app = _app("upload", build_mode="ci_pushed", source_kind="direct_upload")
    monkeypatch.setattr("core.app_deploy.driver_for_capability", _explode)

    assert _provision_registry_repo_sync(app.pk) == ""


def test_an_already_provisioned_repo_still_fast_paths(monkeypatch):
    """The pre-existing idempotent fast-path has to win over the new skip, so
    an app that changed build_mode after onboarding keeps its repo rather than
    reporting it has none."""
    app = _app("kept", build_mode="none", source_kind="github")
    app.registry_repo_uri = "123.dkr.ecr.us-west-2.amazonaws.com/acme/shop"
    app.save(update_fields=["registry_repo_uri"])
    monkeypatch.setattr("core.app_deploy.driver_for_capability", _explode)

    assert _provision_registry_repo_sync(app.pk) == app.registry_repo_uri


def test_a_building_app_still_provisions_its_repo(monkeypatch):
    """The negative control: the skip must not swallow the normal path."""
    app = _app("builds", build_mode="platform_build", source_kind="github")
    called = {}

    class _Repo:
        uri = "123.dkr.ecr.us-west-2.amazonaws.com/acme-prebuilt-builds/shop-prebuilt-builds"

    class _Driver:
        def ensure_repo(self, name):
            called["name"] = name
            return _Repo()

    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: _Driver())

    uri = _provision_registry_repo_sync(app.pk)

    assert called["name"] == "acme-prebuilt-builds/shop-prebuilt-builds"
    assert uri == _Repo.uri
