"""Tests for the static-site deploy/teardown activities (#1010).

Covers the orchestration the rest of the feature hangs off and that the
contract explicitly owes coverage for:

* ``ensure_static_site_services`` -- creates exactly two managed-service rows
  (object_store then cdn), in that order, with the cdn config referencing the
  provisioned bucket; idempotent on re-run; un-deletes a soft-deleted row on a
  redeploy-after-teardown rather than colliding on the unique-active
  constraint.
* ``ensure_static_dns`` -- writes a ``CNAME host -> CDN_DOMAIN_NAME`` for each
  PUBLIC static workload (private ones are skipped).
* ``delete_static_dns_records`` -- removes those CNAMEs and swallows
  ``NotFoundError`` for idempotency (#998).

Real Postgres; the driver provision + the DNS driver are replaced with
recording fakes (no real AWS / no real Temporal needed for the sync cores).
"""

from __future__ import annotations

import pytest

from astrolift_services.models import ManagedService, ManagedServiceBinding
from astrolift_workflows.activities import static_site

pytestmark = pytest.mark.django_db


_STATIC_TOML = """
name = "hello"

[[workloads]]
name = "site"
kind = "static_site"
is_public = true
static_spa = true
static_index = "index.html"
"""

# One public + one private static workload -> the private one must be skipped
# for DNS (it has no public hostname).
_PUBLIC_AND_PRIVATE_TOML = """
name = "hello"

[[workloads]]
name = "site"
kind = "static_site"
is_public = true

[[workloads]]
name = "internal"
kind = "static_site"
is_public = false
"""


# ---- helpers --------------------------------------------------------------


def _managed_domain(org):
    from astrolift_clusters.models import ManagedDomain

    return ManagedDomain.objects.create(
        zone="apps.example.com",
        organization=org,
        dns_driver="route53",
        dns_config={"cloudfront_certificate_arn": "arn:aws:acm:us-east-1:1:certificate/abc"},
    )


def _attach(app, env, org, *, toml=_STATIC_TOML, with_domain=True):
    app.manifest_raw = toml
    app.save(update_fields=["manifest_raw", "updated_at", "version"])
    if with_domain:
        env.managed_domain = _managed_domain(org)
        env.save(update_fields=["managed_domain", "updated_at", "version"])


def _deployment(app, env):
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        image_tag="",
        commit_sha="abc1234",
    )


def _fake_provision(row) -> None:
    """Stand in for the managed-service lifecycle: stamp a backend_ref keyed
    by the row name (so the cdn can read the bucket's name) and flip ACTIVE."""
    if not row.backend_ref:
        row.backend_ref = (
            f"object_store/acme-{row.name}" if row.kind == "object_store" else f"cdn/E-{row.name}"
        )
    row.status = ManagedService.Status.ACTIVE
    row.save(update_fields=["backend_ref", "status", "updated_at", "version"])


class _FakeDns:
    def __init__(self, *, raise_notfound: bool = False):
        self.ensured: list[tuple] = []
        self.deleted: list[tuple] = []
        self._raise_notfound = raise_notfound

    def ensure_record(self, *, zone, name, type, value, ttl):  # noqa: A002 -- driver kwarg
        self.ensured.append((zone, name, type, value, ttl))

    def delete_record(self, zone, name, record_type):
        if self._raise_notfound:
            from aws._errors import NotFoundError

            raise NotFoundError(f"{name} already gone")
        self.deleted.append((zone, name, record_type))


# ---- ensure_static_site_services -----------------------------------------


def test_ensure_creates_two_rows_bucket_then_cdn(monkeypatch, app, env, org):
    _attach(app, env, org)
    monkeypatch.setattr(static_site, "_provision_row", _fake_provision)
    dep = _deployment(app, env)

    out = static_site._ensure_static_site_services_sync(dep.pk)

    rows = ManagedService.objects.filter(registered_app=app)
    assert rows.count() == 2
    bucket = rows.get(kind="object_store")
    cdn = rows.get(kind="cdn")
    # Env-scoped names -- one bucket + distribution per (app, env).
    assert bucket.name == "site-prod-assets"
    assert cdn.name == "site-prod-cdn"
    # Ordering proof: the cdn config could only reference the bucket name if the
    # bucket was provisioned to ACTIVE and its backend_ref read FIRST.
    assert cdn.config["origin_bucket"] == "acme-site-prod-assets"
    # Public + cert present -> alias + cert flow into the distribution config.
    assert cdn.config["aliases"] == ["hello-app.apps.example.com"]
    assert cdn.config["acm_cert_arn"].endswith("certificate/abc")
    assert cdn.config["spa"] is True
    assert out["stub"] is False
    assert out["ensured"][0]["bucket"] == "acme-site-prod-assets"


def test_ensure_is_idempotent_on_rerun(monkeypatch, app, env, org):
    _attach(app, env, org)
    monkeypatch.setattr(static_site, "_provision_row", _fake_provision)
    dep = _deployment(app, env)

    static_site._ensure_static_site_services_sync(dep.pk)
    static_site._ensure_static_site_services_sync(dep.pk)

    assert ManagedService.objects.filter(registered_app=app).count() == 2
    assert ManagedService.all_objects.filter(registered_app=app).count() == 2


def test_ensure_undeletes_soft_deleted_rows_on_redeploy(monkeypatch, app, env, org):
    # Redeploy-after-teardown: the prior rows are soft-deleted. The
    # unique-active constraint is (app, kind, name), so a naive create would
    # IntegrityError; the activity must un-delete the existing rows instead.
    _attach(app, env, org)
    monkeypatch.setattr(static_site, "_provision_row", _fake_provision)
    dep = _deployment(app, env)

    static_site._ensure_static_site_services_sync(dep.pk)
    for row in ManagedService.objects.filter(registered_app=app):
        row.soft_delete()
    assert ManagedService.objects.filter(registered_app=app).count() == 0

    static_site._ensure_static_site_services_sync(dep.pk)
    assert ManagedService.objects.filter(registered_app=app).count() == 2
    # No duplicates left behind in the unscoped table.
    assert ManagedService.all_objects.filter(registered_app=app).count() == 2


def test_ensure_stub_when_no_static_workload(monkeypatch, app, env, org):
    app.manifest_raw = ""
    app.save(update_fields=["manifest_raw", "updated_at", "version"])
    monkeypatch.setattr(static_site, "_provision_row", _fake_provision)
    dep = _deployment(app, env)
    out = static_site._ensure_static_site_services_sync(dep.pk)
    assert out["stub"] is True
    assert ManagedService.objects.filter(registered_app=app).count() == 0


# ---- ensure_static_dns ----------------------------------------------------


def test_ensure_static_dns_writes_cname_for_public_only(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_PUBLIC_AND_PRIVATE_TOML)
    monkeypatch.setattr(static_site, "_provision_row", _fake_provision)
    dep = _deployment(app, env)
    static_site._ensure_static_site_services_sync(dep.pk)

    cdn = ManagedService.objects.get(registered_app=app, kind="cdn", name="site-prod-cdn")
    ManagedServiceBinding.objects.create(
        managed_service=cdn, env_key="CDN_DOMAIN_NAME", env_value_ref="d123.cloudfront.net"
    )

    fake_dns = _FakeDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, cap: fake_dns)

    out = static_site._ensure_static_dns_sync(dep.pk)

    # Exactly one CNAME -- the public "site" workload -> its CDN domain; the
    # private "internal" workload is skipped.
    assert fake_dns.ensured == [
        ("apps.example.com", "hello-app.apps.example.com", "CNAME", "d123.cloudfront.net", 300)
    ]
    assert out["records"] == [{"host": "hello-app.apps.example.com", "value": "d123.cloudfront.net"}]


def test_ensure_static_dns_stub_without_managed_domain(monkeypatch, app, env, org):
    _attach(app, env, org, with_domain=False)
    dep = _deployment(app, env)
    out = static_site._ensure_static_dns_sync(dep.pk)
    assert out["stub"] is True


# ---- delete_static_dns_records (teardown) --------------------------------


def test_delete_static_dns_records(monkeypatch, app, env, org):
    _attach(app, env, org)
    fake_dns = _FakeDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, cap: fake_dns)

    out = static_site._delete_static_dns_records_sync(app.pk)

    assert fake_dns.deleted == [("apps.example.com", "hello-app.apps.example.com", "CNAME")]
    assert out["deleted"] == ["hello-app.apps.example.com"]


def test_delete_static_dns_records_swallows_notfound(monkeypatch, app, env, org):
    _attach(app, env, org)
    fake_dns = _FakeDns(raise_notfound=True)
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, cap: fake_dns)

    # NotFoundError must be swallowed (#998) so a re-run / partial teardown
    # completes instead of hard-failing.
    out = static_site._delete_static_dns_records_sync(app.pk)
    assert out["deleted"] == []
