"""Tests for the provider-managed FaaS deploy/teardown activities (#987/#1035).

Covers the orchestration Stage C owes:

* ``ensure_faas_services`` -- no-op when no faas workload; provisions a single
  ``faas`` (lambda) row for a private function; provisions ``faas`` THEN an
  ``api_gateway`` row (the HTTP API that proxies to the function) for a public
  function; image-mode config carries the built ``<repo>@<digest>``; zip-mode
  shapes runtime/handler; idempotent on re-run; un-deletes soft-deleted rows on
  a redeploy.
* the api_gateway row's ``lambda_function_arn`` could only be built once the
  lambda row provisioned to ACTIVE -- proving the lambda-before-api ordering.
* public faas is NOT cdn-backed (#1035 pivot): no CloudFront cert + no
  ``CNAME -> CloudFront`` record is owned for a faas workload; its public
  surface is the API Gateway ``execute-api`` URL.
* deploy wiring ordering: build_image before ensure_faas_services before
  ensure_workload_identity (so the image is in ECR + grants fold into the
  exec role at the right point).

Real Postgres; the managed-service provision is replaced with a recording fake,
so the api_gateway row can be asserted without real AWS / Temporal.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from astrolift_services.models import ManagedService
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


def _attach(app, env, org, *, toml=_FAAS_PUBLIC_TOML, repo=_REPO):
    app.manifest_raw = toml
    app.registry_repo_uri = repo
    app.save(update_fields=["manifest_raw", "registry_repo_uri", "updated_at", "version"])


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
    the row name and flip ACTIVE. The api_gateway row's resource id is the HTTP
    API id the real driver would mint."""
    if not row.backend_ref:
        if row.kind == "faas":
            row.backend_ref = f"faas/{row.name}"
        elif row.kind == "api_gateway":
            row.backend_ref = f"api_gateway/api-{row.name}"
        else:
            row.backend_ref = f"{row.kind}/{row.name}"
    row.status = ManagedService.Status.ACTIVE
    row.save(update_fields=["backend_ref", "status", "updated_at", "version"])


def _patch_account(monkeypatch, *, account: str = "123456789012") -> None:
    """Stub account-id resolution so the public path builds the Lambda ARN
    without a real STS call."""
    monkeypatch.setattr(faas, "_resolve_account_id", lambda cluster, acct: account)


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
    _attach(app, env, org, toml=_FAAS_PRIVATE_TOML)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    dep = _deployment(app, env)

    out = faas._ensure_faas_services_sync(dep.pk)

    rows = ManagedService.objects.filter(registered_app=app)
    # Private faas: exactly one lambda row, no api_gateway (the falsifiable half
    # -- a public-by-default bug would create a second api_gateway row here).
    assert rows.count() == 1
    fn = rows.get(kind="faas")
    assert fn.name == "api-prod-fn"
    assert fn.variant == "lambda"
    assert fn.config["package_type"] == "image"
    assert fn.config["public"] is False
    assert fn.config["image_uri"] == f"{_REPO}@sha256:abc123"
    assert out["ensured"][0]["function"] == "api-prod-fn"
    assert "api_id" not in out["ensured"][0]


def test_ensure_public_faas_provisions_lambda_then_api_gateway(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_FAAS_PUBLIC_TOML)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    _patch_account(monkeypatch)
    dep = _deployment(app, env)

    out = faas._ensure_faas_services_sync(dep.pk)

    rows = ManagedService.objects.filter(registered_app=app)
    assert rows.count() == 2
    fn = rows.get(kind="faas")
    api = rows.get(kind="api_gateway")
    assert fn.name == "api-prod-fn"
    assert api.name == "api-prod-api"
    assert api.variant == "http_api"
    # The Lambda needs no Function URL on the apigw path (#1035 pivot).
    assert fn.config["public"] is False
    # Ordering proof: the api_gateway row's integration target is the function
    # ARN, which could only be built once the lambda row provisioned (its
    # resolved function name flows into the ARN).
    assert api.config["lambda_function_arn"].endswith(":function:api-prod-fn")
    assert "123456789012" in api.config["lambda_function_arn"]
    # No cdn row is created on the public faas path anymore.
    assert rows.filter(kind="cdn").count() == 0
    assert out["ensured"][0]["api_id"] == "api-api-prod-api"


def test_ensure_public_faas_raises_without_account_id(monkeypatch, app, env, org):
    # The Lambda ARN needs the account id; if it can't be resolved the activity
    # must fail loudly rather than build a malformed ARN.
    _attach(app, env, org, toml=_FAAS_PUBLIC_TOML)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    monkeypatch.setattr(faas, "_resolve_account_id", lambda cluster, acct: "")
    dep = _deployment(app, env)

    with pytest.raises(RuntimeError, match="account id"):
        faas._ensure_faas_services_sync(dep.pk)


def test_ensure_zip_mode_shapes_runtime_handler(monkeypatch, app, env, org):
    _attach(app, env, org, toml=_FAAS_ZIP_TOML)
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
    _patch_account(monkeypatch)
    dep = _deployment(app, env)

    faas._ensure_faas_services_sync(dep.pk)
    faas._ensure_faas_services_sync(dep.pk)

    assert ManagedService.objects.filter(registered_app=app).count() == 2
    assert ManagedService.all_objects.filter(registered_app=app).count() == 2


def test_ensure_undeletes_soft_deleted_rows_on_redeploy(monkeypatch, app, env, org):
    _attach(app, env, org)
    monkeypatch.setattr(faas, "_provision_row", _fake_provision)
    _patch_account(monkeypatch)
    dep = _deployment(app, env)

    faas._ensure_faas_services_sync(dep.pk)
    for row in ManagedService.objects.filter(registered_app=app):
        row.soft_delete()
    assert ManagedService.objects.filter(registered_app=app).count() == 0

    faas._ensure_faas_services_sync(dep.pk)
    assert ManagedService.objects.filter(registered_app=app).count() == 2
    # No duplicates in the unscoped table (unique-active constraint honored).
    assert ManagedService.all_objects.filter(registered_app=app).count() == 2


# ---- public faas is NOT cdn-backed (#1035 pivot) --------------------------


def test_public_faas_is_not_cdn_backed():
    # #1035 pivot: a public faas is reached over its API Gateway execute-api
    # URL, NOT a CloudFront distribution -- so it must NOT appear in the
    # cdn-backed filter that drives the us-east-1 cert + CNAME->CloudFront
    # record. Only static_site is cdn-backed. Falsifiable: re-including faas
    # would mint a pointless ACM cert and target a cdn row never created.
    from astrolift_manifest.normalize import normalize
    from astrolift_manifest.types import RawManifest, WorkloadManifest

    n = normalize(
        RawManifest(
            name="hello",
            workloads=(
                WorkloadManifest(name="site", kind="static_site", is_public=True),
                WorkloadManifest(name="both", kind="faas", is_public=True, faas_public=True),
                WorkloadManifest(name="faaspub", kind="faas", is_public=False, faas_public=True),
            ),
        )
    )
    names = {w.name for w in static_site._cdn_backed_public_workloads(n)}
    assert names == {"site"}


def test_delete_static_dns_skips_public_faas(monkeypatch, app, env, org):
    # Teardown counterpart: a faas-only app owns no CNAME->CloudFront record, so
    # delete_static_dns_records is a no-op (no DNS driver call at all).
    _attach(app, env, org, toml=_FAAS_PUBLIC_TOML)

    called: list[tuple] = []
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability",
        lambda cluster, cap: called.append((cluster, cap)),
    )

    out = static_site._delete_static_dns_records_sync(app.pk)

    assert out["deleted"] == []
    assert called == []


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
    """ensure_faas_services must run AFTER build_image (image in ECR) and
    BEFORE ensure_workload_identity (so bound grants fold into the exec role).
    Guards the contract ordering against an accidental reorder."""
    order = _deploy_activity_call_order()
    for name in ("build_image", "ensure_faas_services", "ensure_workload_identity"):
        assert name in order, name
    assert (
        order.index("build_image")
        < order.index("ensure_faas_services")
        < order.index("ensure_workload_identity")
    )
