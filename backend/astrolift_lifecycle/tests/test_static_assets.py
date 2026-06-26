"""Tests for the static-site asset pipeline (#1010).

Covers the CI-pushed REST upload endpoint (``cli_views.ci_static_upload``)
and the in-cluster sync mode-select rule (``static_site._sync_one_workload``).
Real Postgres; boto3 + the CloudFront driver are replaced with recording
fakes (moto is not installed).
"""

from __future__ import annotations

import io
import tarfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from astrolift_lifecycle.deploy_tokens import issue_token
from astrolift_manifest.types import WorkloadManifest
from astrolift_services.models import ManagedService
from astrolift_workflows.activities import static_site

pytestmark = pytest.mark.django_db

_UPLOAD_URL = "/api/cli/v1/apps/{slug}/static/{workload}/upload/"


# ---- helpers --------------------------------------------------------------


def _targz(files: dict[str, bytes]) -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return bio.getvalue()


class FakeS3:
    """Records put/delete + serves a fixed listing for the --delete pass."""

    def __init__(self, existing: list[str] | None = None):
        self.puts: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []
        self._existing = list(existing or [])

    def put_object(self, *, Bucket, Key, Body, ContentType):  # noqa: N803 -- boto3 kwargs
        self.puts.append((Bucket, Key, ContentType))

    def get_paginator(self, _name):
        existing = self._existing

        class _Pager:
            def paginate(self, *, Bucket, Prefix=""):  # noqa: N803
                keys = [k for k in existing if k.startswith(Prefix)]
                return [{"Contents": [{"Key": k} for k in keys]}]

        return _Pager()

    def delete_objects(self, *, Bucket, Delete):  # noqa: N803
        self.deleted.extend(o["Key"] for o in Delete["Objects"])


class FakeCdnDriver:
    def __init__(self):
        self.invalidated: list[tuple[str, list[str]]] = []

    def invalidate(self, distribution_id, paths=None):
        self.invalidated.append((distribution_id, paths or ["/*"]))
        return {"invalidation_id": "I-123"}


def _active_static_services(app, env, workload="site"):
    # Rows are env-scoped (one bucket + distribution per (app, env)) so a
    # second env never collides with this env's bucket -- mirror the names the
    # platform mints via ``_service_names``.
    assets_name, cdn_name = static_site._service_names(workload, env)
    assets = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="object_store",
        name=assets_name,
        variant="s3",
        status=ManagedService.Status.ACTIVE,
        backend_ref="object_store/acme-site-assets",
        config={"size": "small"},
    )
    cdn = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="cdn",
        name=cdn_name,
        variant="cloudfront",
        status=ManagedService.Status.ACTIVE,
        backend_ref="cdn/E123ABC",
        config={"size": "small", "origin_bucket": "acme-site-assets"},
    )
    return assets, cdn


def _auth(plaintext):
    return {"HTTP_AUTHORIZATION": f"Bearer {plaintext}"}


# ---- ci_static_upload endpoint -------------------------------------------


def test_ci_static_upload_happy_path(monkeypatch, app, env):
    _active_static_services(app, env)
    _row, plaintext = issue_token(app=app, name="ci", scopes=["app.deploy"])

    fake_s3 = FakeS3()
    fake_cdn = FakeCdnDriver()
    monkeypatch.setattr("astrolift_lifecycle.cli_views._s3_client", lambda region: fake_s3)
    monkeypatch.setattr(static_site, "_cdn_driver", lambda cluster, cdn=None: fake_cdn)

    bundle = SimpleUploadedFile(
        "site.tar.gz",
        _targz({"index.html": b"<h1>hi</h1>", "assets/app.js": b"console.log(1)"}),
        content_type="application/gzip",
    )
    resp = Client().post(
        _UPLOAD_URL.format(slug=app.slug, workload="site"),
        data={"bundle": bundle},
        **_auth(plaintext),
    )

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["ok"] is True
    assert body["bucket"] == "acme-site-assets"
    assert body["objects"] == 2
    assert body["invalidation_id"] == "I-123"
    # Both files uploaded to the bucket, CDN invalidated for this distribution.
    keys = {k for _b, k, _c in fake_s3.puts}
    assert keys == {"index.html", "assets/app.js"}
    assert fake_cdn.invalidated == [("E123ABC", ["/*"])]


def test_ci_static_upload_honors_prefix_and_deletes_extras(monkeypatch, app, env):
    _active_static_services(app, env)
    _row, plaintext = issue_token(app=app, name="ci", scopes=["app.deploy"])

    # A stale object under the prefix not present in the new bundle.
    fake_s3 = FakeS3(existing=["v2/old.html"])
    monkeypatch.setattr("astrolift_lifecycle.cli_views._s3_client", lambda region: fake_s3)
    monkeypatch.setattr(static_site, "_cdn_driver", lambda cluster, cdn=None: FakeCdnDriver())

    bundle = SimpleUploadedFile("b.tar.gz", _targz({"index.html": b"x"}), content_type="application/gzip")
    resp = Client().post(
        _UPLOAD_URL.format(slug=app.slug, workload="site"),
        data={"bundle": bundle, "prefix": "v2"},
        **_auth(plaintext),
    )
    assert resp.status_code == 200
    assert ("acme-site-assets", "v2/index.html", "text/html") in fake_s3.puts
    assert fake_s3.deleted == ["v2/old.html"]


def test_ci_static_upload_requires_token(app, env):
    _active_static_services(app, env)
    resp = Client().post(_UPLOAD_URL.format(slug=app.slug, workload="site"), data={})
    assert resp.status_code == 401


def test_ci_static_upload_rejects_cross_app_token(app, env, org, project, team):
    from astrolift_registry.models import RegisteredApp

    _active_static_services(app, env)
    other = RegisteredApp.objects.create(
        organization=org, project=project, team=team, name="Other", slug="other-app"
    )
    _row, plaintext = issue_token(app=other, name="ci", scopes=["app.deploy"])
    resp = Client().post(
        _UPLOAD_URL.format(slug=app.slug, workload="site"),
        data={"bundle": SimpleUploadedFile("b.tar.gz", _targz({"i": b"x"}))},
        **_auth(plaintext),
    )
    assert resp.status_code == 403


def test_ci_static_upload_409_when_not_provisioned(app, env):
    # No managed-service rows for the workload yet.
    _row, plaintext = issue_token(app=app, name="ci", scopes=["app.deploy"])
    resp = Client().post(
        _UPLOAD_URL.format(slug=app.slug, workload="site"),
        data={"bundle": SimpleUploadedFile("b.tar.gz", _targz({"i": b"x"}))},
        **_auth(plaintext),
    )
    assert resp.status_code == 409
    assert "not provisioned" in resp.json()["detail"]


def test_ci_static_upload_400_on_malformed_bundle(monkeypatch, app, env):
    _active_static_services(app, env)
    _row, plaintext = issue_token(app=app, name="ci", scopes=["app.deploy"])
    monkeypatch.setattr("astrolift_lifecycle.cli_views._s3_client", lambda region: FakeS3())
    monkeypatch.setattr(static_site, "_cdn_driver", lambda cluster, cdn=None: FakeCdnDriver())

    bundle = SimpleUploadedFile("b.bin", b"not a tarball or zip", content_type="application/octet-stream")
    resp = Client().post(
        _UPLOAD_URL.format(slug=app.slug, workload="site"),
        data={"bundle": bundle},
        **_auth(plaintext),
    )
    assert resp.status_code == 400


# ---- mode-select rule -----------------------------------------------------


def test_sync_mode_select_ci_pushed_when_no_build_command(monkeypatch, app, env, cluster):
    _active_static_services(app, env)
    fake_cdn = FakeCdnDriver()
    monkeypatch.setattr(static_site, "_cdn_driver", lambda c, cdn=None: fake_cdn)

    w = WorkloadManifest(name="site", kind="static_site", static_build_command="", static_output_dir="")
    out = static_site._sync_one_workload(
        deployment=None,
        app=app,
        env=env,
        cluster=cluster,
        workload=w,
        region="us-west-2",
        account_id="1",
        commit_sha="",
    )
    assert out["mode"] == "ci_pushed"
    assert out["synced"] is False
    # CI-pushed mode still busts the cache best-effort.
    assert fake_cdn.invalidated == [("E123ABC", ["/*"])]


def test_sync_mode_select_platform_build_when_build_command_set(monkeypatch, app, env, cluster):
    _active_static_services(app, env)
    calls: list[str] = []

    def _fake_platform_build(*, workload, **_kw):
        calls.append(workload.name)
        return {"workload": workload.name, "mode": "platform_build", "synced": True}

    monkeypatch.setattr(static_site, "_platform_build_workload", _fake_platform_build)

    w = WorkloadManifest(
        name="site", kind="static_site", static_build_command="npm run build", static_output_dir="dist"
    )
    out = static_site._sync_one_workload(
        deployment=None,
        app=app,
        env=env,
        cluster=cluster,
        workload=w,
        region="us-west-2",
        account_id="1",
        commit_sha="abc",
    )
    assert out["mode"] == "platform_build"
    assert calls == ["site"]


def test_sync_one_workload_stub_when_services_not_active(app, env, cluster):
    # Rows exist but are still PENDING — nothing to sync against yet.
    assets_name, _cdn = static_site._service_names("site", env)
    ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="object_store",
        name=assets_name,
        variant="s3",
        status=ManagedService.Status.PENDING,
    )
    w = WorkloadManifest(name="site", kind="static_site")
    out = static_site._sync_one_workload(
        deployment=None,
        app=app,
        env=env,
        cluster=cluster,
        workload=w,
        region="r",
        account_id="1",
        commit_sha="",
    )
    assert out["stub"] is True


# ---- platform-build failure propagation ----------------------------------


def test_platform_build_raises_on_failed_build(monkeypatch, app, cluster):
    """A failed in-cluster build Job must raise (terminal _ROLLOUT_RETRY
    signal) -- not return success -- or a broken deploy reports green."""
    import importlib
    import types

    import core.app_deploy as app_deploy
    import core.cluster_management as cluster_management
    import providers.k8s_native.build_static as build_static_mod

    # ``astrolift_workflows.activities.build_image`` resolves to the activity
    # function (re-exported by the package __init__), so reach the module.
    build_image = importlib.import_module("astrolift_workflows.activities.build_image")

    class _FakeIdentity:
        def create_identity_role(self, name, perms):
            return None

        def bind_service_account(self, slug, ns, sa, role):
            return {"eks.amazonaws.com/role-arn": "arn:aws:iam::1:role/x"}

    class _FakeBuildDriver:
        def __init__(self, **_kw):
            pass

        def build(self, **_kw):
            return types.SimpleNamespace(success=False, errors=["boom"])

    monkeypatch.setattr(build_image, "_ensure_cluster_oidc_issuer", lambda c: None)
    monkeypatch.setattr(build_image, "_resolve_source_url", lambda a, s: "git+https://example.com/r#main")
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda c, cap: _FakeIdentity())
    monkeypatch.setattr(
        cluster_management, "_context_for_cluster", lambda c: types.SimpleNamespace(slug=c.slug)
    )
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: object())
    monkeypatch.setattr(build_static_mod, "StaticAssetBuildDriver", _FakeBuildDriver)

    w = WorkloadManifest(
        name="site", kind="static_site", static_build_command="npm run build", static_output_dir="dist"
    )
    with pytest.raises(RuntimeError, match="static asset build failed"):
        static_site._platform_build_workload(
            deployment=types.SimpleNamespace(pk=1),
            app=app,
            cluster=cluster,
            workload=w,
            bucket="acme-site-assets",
            distribution_id="E1",
            region="us-west-2",
            account_id="1",
            commit_sha="abc",
        )


# ---- _sync_static_assets_sync stub guards --------------------------------


def _deployment(app, env):
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.create(
        registered_app=app, app_environment=env, trigger_kind="manual", image_tag="", commit_sha="s"
    )


_STATIC_TOML = """
name = "hello"

[[workloads]]
name = "site"
kind = "static_site"
is_public = true
"""


def test_sync_static_assets_stub_when_no_static_workload(app, env):
    # Empty manifest -> no static workload -> no-op fast-return.
    dep = _deployment(app, env)
    out = static_site._sync_static_assets_sync(static_site.SyncStaticAssetsInput(dep.pk, ""))
    assert out["stub"] is True


def test_sync_static_assets_stub_when_no_cluster(monkeypatch, app, env):
    app.manifest_raw = _STATIC_TOML
    app.save(update_fields=["manifest_raw", "updated_at", "version"])
    dep = _deployment(app, env)
    from core import app_deploy

    def _raise(_dep):
        raise app_deploy.AppDeployError("no cluster")

    monkeypatch.setattr(app_deploy, "cluster_for_deployment", _raise)
    out = static_site._sync_static_assets_sync(static_site.SyncStaticAssetsInput(dep.pk, ""))
    assert out["stub"] is True


def test_sync_static_assets_stub_on_non_aws_provider(app, env, cluster):
    # The test-provider cluster is not "aws" -> the static build/sync identity
    # path is unwired, so the activity stubs out.
    app.manifest_raw = _STATIC_TOML
    app.save(update_fields=["manifest_raw", "updated_at", "version"])
    dep = _deployment(app, env)
    out = static_site._sync_static_assets_sync(static_site.SyncStaticAssetsInput(dep.pk, ""))
    assert out["stub"] is True


# ---- per-app static-build IRSA permission shape --------------------------


def test_static_build_permissions_scope():
    perms = static_site._static_build_permissions(
        bucket_arn="arn:aws:s3:::acme-site-assets",
        distribution_arn="arn:aws:cloudfront::1:distribution/E123ABC",
    )
    by_action = {tuple(p["Action"]) if isinstance(p["Action"], list) else (p["Action"],): p for p in perms}
    # S3 list scoped to the bucket; object write scoped to /*; CloudFront
    # invalidation scoped to the one distribution.
    assert by_action[("s3:ListBucket", "s3:GetBucketLocation")]["Resource"] == "arn:aws:s3:::acme-site-assets"
    assert by_action[("s3:PutObject", "s3:GetObject", "s3:DeleteObject")]["Resource"] == (
        "arn:aws:s3:::acme-site-assets/*"
    )
    assert by_action[("cloudfront:CreateInvalidation",)]["Resource"] == (
        "arn:aws:cloudfront::1:distribution/E123ABC"
    )
    # All descriptions/values ASCII (#1026).
    import json

    assert json.dumps(perms).isascii()
