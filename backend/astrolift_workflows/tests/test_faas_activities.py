"""Tests for the provider-managed FaaS deploy/teardown activities (#987).

Covers the orchestration Stage C owes:

* ``ensure_faas_services`` -- no-op when no faas workload; provisions a single
  ``faas`` (lambda) row for a private function; provisions ``faas`` then a
  ``cdn`` row (origin = the Function URL host) for a public function; image-mode
  config carries the built ``<repo>@<digest>``; zip-mode shapes runtime/handler;
  idempotent on re-run; un-deletes soft-deleted rows on a redeploy.
* the generalized ``ensure_static_dns`` / ``delete_static_dns_records`` cover a
  public faas workload (the CNAME -> the faas cdn distribution).
* deploy wiring ordering: build_image before ensure_faas_services before
  ensure_workload_identity (so the image is in ECR + grants fold into the
  exec role at the right point).

Real Postgres; the managed-service provision is replaced with a recording fake
that also materializes the FUNCTION_URL binding (as the real finalize does), so
the cdn origin can be read without real AWS / Temporal.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from astrolift_services.models import ManagedService, ManagedServiceBinding
from astrolift_workflows.activities import faas, static_site

pytestmark = pytest.mark.django_db


_FAAS_PUBLIC_TOML = """
name = "hello"

[[workloads]]
name = "api"
kind = "faas"
is_public = true
faas_public = true
"""

_FAAS_PRIVATE_TOML = """
name = "hello"

[[workloads]]
name = "api"
kind = "faas"
"""

# faasprobe-shape (#1035 live): the operator sets ONLY faas_public, not
# is_public. normalize must imply is_public so the alias + CNAME fire.
_FAAS_PUBLIC_ONLY_TOML = """
name = "hello"

[[workloads]]
name = "api"
kind = "faas"
faas_public = true
"""

_FAAS_ZIP_TOML = """
name = "hello"

[[workloads]]
name = "api"
kind = "faas"
faas_package_type = "zip"
faas_runtime = "python3.12"
faas_handler = "app.handler"
faas_output_dir = "build"
"""

_REPO = "123.dkr.ecr.us-east-1.amazonaws.com/acme/hello-app"


# ---- helpers --------------------------------------------------------------


def _managed_domain(org):
    from astrolift_clusters.models import ManagedDomain

    return ManagedDomain.objects.create(
        zone="apps.example.com",
        organization=org,
        dns_driver="route53",
        dns_config={"cloudfront_certificate_arn": "arn:aws:acm:us-east-1:1:certificate/abc"},
    )


def _attach(app, env, org, *, toml=_FAAS_PUBLIC_TOML, with_domain=True, repo=_REPO):
    app.manifest_raw = toml
    app.registry_repo_uri = repo
    app.save(update_fields=["manifest_raw", "registry_repo_uri", "updated_at", "version"])
    if with_domain:
        env.managed_domain = _managed_domain(org)
        env.save(update_fields=["managed_domain", "updated_at", "version"])


def _deployment(app, env, *, digest="sha256:abc123", image_tag="v1"):
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        image_tag=image_tag,
        image_digest=digest,
        commit_sha="abc1234",
    )


def _fake_provision(row) -> None:
    """Stand in for the managed-service lifecycle: stamp a backend_ref keyed by
    the row name and flip ACTIVE; for a faas row also materialize the
    FUNCTION_URL binding exactly as the real finalize does, so the cdn can read
    the Function URL host for its origin."""
    if not row.backend_ref:
        if row.kind == "faas":
            row.backend_ref = f"faas/{row.name}"
        elif row.kind == "cdn":
            row.backend_ref = f"cdn/E-{row.name}"
        else:
            row.backend_ref = f"{row.kind}/{row.name}"
    row.status = ManagedService.Status.ACTIVE
    row.save(update_fields=["backend_ref", "status", "updated_at", "version"])
    if row.kind == "faas":
        ManagedServiceBinding.objects.get_or_create(
            managed_service=row,
            env_key="FUNCTION_URL",
            defaults={"env_value_ref": f"https://{row.name}.lambda-url.us-east-1.on.aws/"},
        )


def _patch_public_grant(monkeypatch, *, account: str = "123456789012") -> list[tuple[str, str]]:
    """Patch the post-cdn CloudFront invoke grant (#1035) with recorders so the
    public path runs without real AWS. Returns the list of
    (function_name, distribution_arn) grants made."""
    grants: list[tuple[str, str]] = []

    class _Drv:
        def __init__(self, *a, **k) -> None:
            pass

        def allow_cloudfront_invoke(self, function_name: str, distribution_arn: str) -> None:
            grants.append((function_name, distribution_arn))

    monkeypatch.setattr(faas, "_faas_driver", lambda cluster, row: _Drv())
    monkeypatch.setattr(faas, "_resolve_account_id", lambda cluster, acct: account)
    return grants


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


# ---- ensure_faas_services -------------------------------------------------


def test_ensure_stub_when_no_faas_workload(monkeypatch, app, env, org):
    app.manifest_raw = ""
    app.save(update_fields=["manifest_raw", "updated_at", "version"])
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    dep = _deployment(app, env)

    out = faas._ensure_faas_services_sync(dep.pk)

    assert out["stub"] is True
    assert ManagedService.objects.filter(registered_app=app).count() == 0


def test_ensure_private_faas_provisions_lambda_only(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_FAAS_PRIVATE_TOML, with_domain=False)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    dep = _deployment(app, env)

    out = faas._ensure_faas_services_sync(dep.pk)

    rows = ManagedService.objects.filter(registered_app=app)
    # Private faas: exactly one lambda row, no cdn (the falsifiable half --
    # a public-by-default bug would create a second cdn row here).
    assert rows.count() == 1
    fn = rows.get(kind="faas")
    assert fn.name == "api-prod-fn"
    assert fn.variant == "lambda"
    assert fn.config["package_type"] == "image"
    assert fn.config["public"] is False
    # Image mode resolves the immutable <repo>@<digest> the build recorded.
    assert fn.config["image_uri"] == f"{_REPO}@sha256:abc123"
    assert out["ensured"][0]["function"] == "api-prod-fn"
    assert "distribution_id" not in out["ensured"][0]


def test_ensure_public_faas_provisions_lambda_then_cdn(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_FAAS_PUBLIC_TOML)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    grants = _patch_public_grant(monkeypatch)
    dep = _deployment(app, env)

    out = faas._ensure_faas_services_sync(dep.pk)

    rows = ManagedService.objects.filter(registered_app=app)
    assert rows.count() == 2
    fn = rows.get(kind="faas")
    cdn = rows.get(kind="cdn")
    assert fn.name == "api-prod-fn"
    assert cdn.name == "api-prod-cdn"
    assert fn.config["public"] is True
    # Ordering proof: the cdn's custom origin could only be the Function URL
    # host if the lambda was provisioned to ACTIVE and its FUNCTION_URL read
    # FIRST. CloudFront's origin must be the bare host (no scheme/path).
    assert cdn.config["custom_origin_domain"] == "api-prod-fn.lambda-url.us-east-1.on.aws"
    assert "origin_bucket" not in cdn.config  # custom (non-S3) origin
    # Public + cert present -> alias + cert flow into the distribution.
    assert cdn.config["aliases"] == ["hello-app.apps.example.com"]
    assert cdn.config["acm_cert_arn"].endswith("certificate/abc")
    assert out["ensured"][0]["origin"] == "api-prod-fn.lambda-url.us-east-1.on.aws"
    # POST-cdn (#1035): the Lambda invoke is scoped to THIS distribution's ARN.
    # Falsifiable: dropping the post-cdn grant (or building it before the cdn)
    # leaves grants empty.
    distribution_id = out["ensured"][0]["distribution_id"]
    assert grants == [("api-prod-fn", f"arn:aws:cloudfront::123456789012:distribution/{distribution_id}")]


def test_ensure_zip_mode_shapes_runtime_handler(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_FAAS_ZIP_TOML, with_domain=False)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    dep = _deployment(app, env)

    faas._ensure_faas_services_sync(dep.pk)

    fn = ManagedService.objects.get(registered_app=app, kind="faas")
    assert fn.config["package_type"] == "zip"
    assert fn.config["runtime"] == "python3.12"
    assert fn.config["handler"] == "app.handler"
    # Zip build pipeline is a follow-up; image_uri is not a zip-mode key.
    assert "image_uri" not in fn.config


def test_ensure_is_idempotent_on_rerun(monkeypatch, app, env, org):
    _attach(app, env, org)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    _patch_public_grant(monkeypatch)
    dep = _deployment(app, env)

    faas._ensure_faas_services_sync(dep.pk)
    faas._ensure_faas_services_sync(dep.pk)

    assert ManagedService.objects.filter(registered_app=app).count() == 2
    assert ManagedService.all_objects.filter(registered_app=app).count() == 2


def test_ensure_undeletes_soft_deleted_rows_on_redeploy(monkeypatch, app, env, org):
    _attach(app, env, org)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    _patch_public_grant(monkeypatch)
    dep = _deployment(app, env)

    faas._ensure_faas_services_sync(dep.pk)
    for row in ManagedService.objects.filter(registered_app=app):
        row.soft_delete()
    assert ManagedService.objects.filter(registered_app=app).count() == 0

    faas._ensure_faas_services_sync(dep.pk)
    assert ManagedService.objects.filter(registered_app=app).count() == 2
    # No duplicates in the unscoped table (unique-active constraint honored).
    assert ManagedService.all_objects.filter(registered_app=app).count() == 2


# ---- generalized DNS covers public faas -----------------------------------


def test_static_dns_writes_cname_for_public_faas(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_FAAS_PUBLIC_TOML)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    _patch_public_grant(monkeypatch)
    dep = _deployment(app, env)
    faas._ensure_faas_services_sync(dep.pk)

    # The faas cdn row carries the same CDN_DOMAIN_NAME binding a static cdn
    # does; the generalized ensure_static_dns resolves it via the shared
    # {workload}-{env}-cdn name.
    cdn = ManagedService.objects.get(registered_app=app, kind="cdn", name="api-prod-cdn")
    ManagedServiceBinding.objects.create(
        managed_service=cdn, env_key="CDN_DOMAIN_NAME", env_value_ref="d999.cloudfront.net"
    )

    fake_dns = _FakeDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, cap: fake_dns)

    out = static_site._ensure_static_dns_sync(dep.pk)

    assert fake_dns.ensured == [
        ("apps.example.com", "hello-app.apps.example.com", "CNAME", "d999.cloudfront.net", 300)
    ]
    assert out["records"] == [{"host": "hello-app.apps.example.com", "value": "d999.cloudfront.net"}]


def test_faas_public_only_implies_is_public_and_writes_cname(monkeypatch, app, env, org):
    # #1035 live root cause: faasprobe declared ONLY faas_public (no is_public),
    # so the cdn was created but got no alias and no CNAME. normalize must imply
    # is_public so the alias flows into the cdn AND ensure_static_dns writes the
    # CNAME. Falsifiable: reverting the normalize implication leaves aliases
    # empty and writes no record.
    _attach(app, env, org, toml=_FAAS_PUBLIC_ONLY_TOML)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    _patch_public_grant(monkeypatch)
    dep = _deployment(app, env)
    faas._ensure_faas_services_sync(dep.pk)

    cdn = ManagedService.objects.get(registered_app=app, kind="cdn", name="api-prod-cdn")
    # The alias was applied (is_public implied) -> custom domain on the dist.
    assert cdn.config["aliases"] == ["hello-app.apps.example.com"]
    ManagedServiceBinding.objects.create(
        managed_service=cdn, env_key="CDN_DOMAIN_NAME", env_value_ref="d999.cloudfront.net"
    )

    fake_dns = _FakeDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, cap: fake_dns)

    out = static_site._ensure_static_dns_sync(dep.pk)

    assert fake_dns.ensured == [
        ("apps.example.com", "hello-app.apps.example.com", "CNAME", "d999.cloudfront.net", 300)
    ]
    assert out["records"] == [{"host": "hello-app.apps.example.com", "value": "d999.cloudfront.net"}]


def test_delete_static_dns_records_covers_public_faas(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_FAAS_PUBLIC_TOML)
    fake_dns = _FakeDns()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, cap: fake_dns)

    out = static_site._delete_static_dns_records_sync(app.pk)

    assert fake_dns.deleted == [("apps.example.com", "hello-app.apps.example.com", "CNAME")]
    assert out["deleted"] == ["hello-app.apps.example.com"]


def test_cdn_backed_filter_keys_on_faas_public():
    # #1035: faas_public is the public switch -- normalize implies is_public from
    # it, so a workload that declares ONLY faas_public ("faaspub") is included.
    # A faas with is_public but NOT faas_public ("nopub") must still be excluded,
    # otherwise the teardown/DNS would target a cdn row that ensure_faas_services
    # never created (faas_public gates the cdn). Falsifiable both ways: dropping
    # the normalize implication drops "faaspub"; dropping the faas_public check
    # would wrongly include "nopub".
    from astrolift_manifest.normalize import normalize
    from astrolift_manifest.types import RawManifest, WorkloadManifest

    n = normalize(
        RawManifest(
            name="hello",
            workloads=(
                WorkloadManifest(name="both", kind="faas", is_public=True, faas_public=True),
                WorkloadManifest(name="nopub", kind="faas", is_public=True, faas_public=False),
                WorkloadManifest(name="faaspub", kind="faas", is_public=False, faas_public=True),
            ),
        )
    )
    names = {w.name for w in static_site._cdn_backed_public_workloads(n)}
    assert names == {"both", "faaspub"}


# ---- deploy wiring ordering ------------------------------------------------


def _deploy_activity_call_order() -> list[str]:
    """The ordered activity names actually passed to ``workflow.execute_activity``
    in ``DeployAppWorkflow.run`` (AST-based so comments mentioning an activity
    name can't confound a plain string-index check)."""
    import ast

    from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow

    tree = ast.parse(textwrap.dedent(inspect.getsource(DeployAppWorkflow.run)))
    # ast.walk is BFS, not source order -- collect with positions and sort so
    # the returned list reflects actual call order in the method body.
    calls: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute_activity"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            calls.append((node.lineno, node.col_offset, node.args[0].id))
    return [name for _ln, _col, name in sorted(calls)]


def test_deploy_wiring_build_before_faas_before_identity():
    """ensure_faas_services must run AFTER build_image (image in ECR) and the
    cert, and BEFORE ensure_workload_identity (so bound grants fold into the
    exec role). Guards the contract ordering against an accidental reorder."""
    order = _deploy_activity_call_order()
    for name in ("build_image", "ensure_cloudfront_cert", "ensure_faas_services", "ensure_workload_identity"):
        assert name in order, name
    assert (
        order.index("build_image")
        < order.index("ensure_cloudfront_cert")
        < order.index("ensure_faas_services")
        < order.index("ensure_workload_identity")
    )
