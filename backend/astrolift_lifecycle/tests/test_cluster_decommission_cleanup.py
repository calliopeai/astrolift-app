"""Tests for the per-app cloud satellite cleanup activities (#354).

Two layers exercised:

1. Activity sync bodies — driver fan-out, NotImplementedError → skipped,
   per-target failure → ``errors`` list, ok=True (best-effort).
2. ``DecommissionClusterWorkflow`` orchestration — record every
   ``execute_activity`` call against a stubbed temporalio.workflow
   surface so we can assert that the four cleanup activities are
   scheduled in the correct order before ``teardown_cluster_infra``.

The driver layer is faked via ``monkeypatch`` on
``astrolift_workflows.activities.cluster_decommission_cleanup._resolve_capability_driver``
so the activity tests run against a record-only stand-in. Real driver
plumbing (boto3 calls etc.) lives in ``astrolift-providers/aws`` and
has its own integration tests.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.models.ingress import IngressRule
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.cluster_decommission_cleanup import (
    _apps_on_cluster,
    _cleanup_acm_certs_sync,
    _cleanup_dns_records_sync,
    _cleanup_ecr_repos_sync,
    _cleanup_irsa_roles_sync,
    _identity_role_name_for,
    _repo_name_from_uri,
    _split_hostname,
)

CLEANUP_MOD = "astrolift_workflows.activities.cluster_decommission_cleanup"


# ---- Fakes ---------------------------------------------------------


class _CallRecorder:
    """Records ``(method, args, kwargs)`` for every call so tests can
    assert what the driver was asked to do. Optional exception map
    triggers per-target failures."""

    def __init__(self, *, raises: dict[Any, BaseException] | None = None):
        self.calls: list[tuple[str, tuple, dict]] = []
        self._raises = raises or {}

    def record(self, method: str):
        def _capture(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            # Trigger configured exception when the first positional
            # arg matches a key in the raises map.
            if args and args[0] in self._raises:
                raise self._raises[args[0]]
            return None

        return _capture


def _install_fake_driver(monkeypatch, *, capability: str, methods: dict[str, Any]):
    """Replace ``_resolve_capability_driver`` with a stand-in returning
    a SimpleNamespace bound to the methods the test expects."""
    fake = SimpleNamespace(**methods)

    def _resolver(cluster, cap):
        assert cap == capability, f"expected capability={capability!r}, got {cap!r}"
        return fake

    monkeypatch.setattr(f"{CLEANUP_MOD}._resolve_capability_driver", _resolver)
    return fake


def _install_unimplemented_driver(monkeypatch, *, capability: str):
    """Make ``_resolve_capability_driver`` raise ``NotImplementedError``
    so the activity treats the whole sweep as skipped."""

    def _resolver(cluster, cap):
        assert cap == capability
        raise NotImplementedError(f"{capability} not implemented for plugin")

    monkeypatch.setattr(f"{CLEANUP_MOD}._resolve_capability_driver", _resolver)


# ---- Fixtures: bind apps to the cluster ---------------------------


@pytest.fixture
def app_on_cluster(app, cluster):
    """The cleanup activities resolve apps via either
    ``RegisteredApp.default_tenant_cluster`` or
    ``AppEnvironment.tenant_cluster``. Bind both so the helper finds
    the app via the union path."""
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster", "updated_at", "version"])
    return app


@pytest.fixture
def second_app_on_cluster(org, project, team, cluster):
    """A second app on the same cluster — exercises the per-app fan-
    out (every activity should call the driver N times)."""
    second = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Second",
        slug="second-app",
        provisioning_status="ready",
    )
    second.default_tenant_cluster = cluster
    second.save(update_fields=["default_tenant_cluster", "updated_at", "version"])
    return second


# ---- pure helpers --------------------------------------------------


def test_repo_name_from_uri_strips_registry_host():
    assert (
        _repo_name_from_uri(
            "123.dkr.ecr.us-west-2.amazonaws.com/astrolift/hello",
            "fallback",
        )
        == "astrolift/hello"
    )


def test_repo_name_from_uri_falls_back_to_app_slug_when_empty():
    assert _repo_name_from_uri("", "hello-app") == "hello-app"


def test_repo_name_from_uri_returns_uri_when_no_slash():
    assert _repo_name_from_uri("plain-repo", "fallback") == "plain-repo"


def test_split_hostname_subdomain():
    assert _split_hostname("api.acme.com") == ("api", "acme.com")


def test_split_hostname_apex():
    assert _split_hostname("acme.com") == ("", "acme.com")


def test_split_hostname_deeper():
    assert _split_hostname("api.staging.acme.com") == ("api", "staging.acme.com")


def test_identity_role_name_for_includes_org_slug():
    fake = SimpleNamespace(slug="hello", organization=SimpleNamespace(slug="acme"))
    assert _identity_role_name_for(fake) == "astrolift-acme-hello"


def test_identity_role_name_for_falls_back_without_org():
    fake = SimpleNamespace(slug="solo", organization=None)
    assert _identity_role_name_for(fake) == "astrolift-solo"


# ---- _apps_on_cluster ---------------------------------------------


@pytest.mark.django_db
def test_apps_on_cluster_finds_default_bound_apps(app_on_cluster, cluster):
    apps = _apps_on_cluster(cluster.pk)
    assert [a.pk for a in apps] == [app_on_cluster.pk]


@pytest.mark.django_db
def test_apps_on_cluster_finds_env_bound_apps(app, cluster, env):
    # ``app`` fixture leaves default_tenant_cluster unset; ``env`` binds
    # the cluster on the env side. Cleanup must still find the app.
    apps = _apps_on_cluster(cluster.pk)
    assert [a.pk for a in apps] == [app.pk]


@pytest.mark.django_db
def test_apps_on_cluster_unions_both_paths(app, cluster, env, second_app_on_cluster):
    apps = _apps_on_cluster(cluster.pk)
    assert {a.pk for a in apps} == {app.pk, second_app_on_cluster.pk}


@pytest.mark.django_db
def test_apps_on_cluster_excludes_soft_deleted(app_on_cluster, cluster):
    app_on_cluster.deleted_at = __import__("datetime").datetime.now(
        __import__("datetime").UTC,
    )
    app_on_cluster.save(update_fields=["deleted_at", "updated_at", "version"])
    apps = _apps_on_cluster(cluster.pk)
    assert apps == []


# ---- cleanup_cluster_irsa_roles -----------------------------------


@pytest.mark.django_db
def test_cleanup_irsa_roles_calls_driver_per_app(
    app_on_cluster,
    second_app_on_cluster,
    cluster,
    org,
    monkeypatch,
):
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="identity",
        methods={"delete_identity_role": rec.record("delete_identity_role")},
    )
    summary = _cleanup_irsa_roles_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 2
    assert summary["errors"] == []
    assert summary["skipped"] is False
    role_names = sorted(args[0] for _, args, _ in rec.calls)
    assert role_names == sorted(
        [
            f"astrolift-{org.slug}-{app_on_cluster.slug}",
            f"astrolift-{org.slug}-{second_app_on_cluster.slug}",
        ],
    )


@pytest.mark.django_db
def test_cleanup_irsa_roles_skipped_on_unimplemented_driver(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    _install_unimplemented_driver(monkeypatch, capability="identity")
    summary = _cleanup_irsa_roles_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["skipped"] is True
    assert summary["deleted"] == 0
    assert "not implemented" in summary["reason"]


@pytest.mark.django_db
def test_cleanup_irsa_roles_collects_per_target_errors(
    app_on_cluster,
    second_app_on_cluster,
    cluster,
    org,
    monkeypatch,
):
    bad_role = f"astrolift-{org.slug}-{app_on_cluster.slug}"
    rec = _CallRecorder(raises={bad_role: RuntimeError("boom")})
    _install_fake_driver(
        monkeypatch,
        capability="identity",
        methods={"delete_identity_role": rec.record("delete_identity_role")},
    )
    summary = _cleanup_irsa_roles_sync(cluster.pk)
    # Best-effort: ok=True even with one failure.
    assert summary["ok"] is True
    assert summary["deleted"] == 1
    assert len(summary["errors"]) == 1
    assert "boom" in summary["errors"][0]
    assert summary["skipped"] is False


@pytest.mark.django_db
def test_cleanup_irsa_roles_no_apps_returns_zero(cluster, monkeypatch):
    # No apps bound — no driver call, returns deleted=0 cleanly.
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="identity",
        methods={"delete_identity_role": rec.record("delete_identity_role")},
    )
    summary = _cleanup_irsa_roles_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 0
    # The driver should never have been touched.
    assert rec.calls == []


@pytest.mark.django_db
def test_cleanup_irsa_roles_skipped_when_driver_lacks_method(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    # Driver exists but doesn't expose delete_identity_role.
    _install_fake_driver(monkeypatch, capability="identity", methods={})
    summary = _cleanup_irsa_roles_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["skipped"] is True
    assert "delete_identity_role" in summary["reason"]


# ---- cleanup_cluster_ecr_repos ------------------------------------


@pytest.mark.django_db
def test_cleanup_ecr_repos_calls_driver_per_app(
    app_on_cluster,
    second_app_on_cluster,
    cluster,
    monkeypatch,
):
    app_on_cluster.registry_repo_uri = "123.dkr.ecr.us-west-2.amazonaws.com/astrolift/hello"
    app_on_cluster.save(update_fields=["registry_repo_uri", "updated_at", "version"])
    # second_app_on_cluster has no registry URI — falls back to slug.
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="registry",
        methods={"delete_repo": rec.record("delete_repo")},
    )
    summary = _cleanup_ecr_repos_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 2
    repo_names = sorted(args[0] for _, args, _ in rec.calls)
    assert repo_names == sorted(["astrolift/hello", second_app_on_cluster.slug])
    # Every call passes archive=True per the cleanup default.
    for _, _, kwargs in rec.calls:
        assert kwargs == {"archive": True}


@pytest.mark.django_db
def test_cleanup_ecr_repos_skipped_on_unimplemented_driver(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    _install_unimplemented_driver(monkeypatch, capability="registry")
    summary = _cleanup_ecr_repos_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["skipped"] is True


@pytest.mark.django_db
def test_cleanup_ecr_repos_collects_per_target_errors(
    app_on_cluster,
    second_app_on_cluster,
    cluster,
    monkeypatch,
):
    rec = _CallRecorder(raises={"second-app": RuntimeError("ecr 500")})
    _install_fake_driver(
        monkeypatch,
        capability="registry",
        methods={"delete_repo": rec.record("delete_repo")},
    )
    summary = _cleanup_ecr_repos_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 1
    assert len(summary["errors"]) == 1
    assert "ecr 500" in summary["errors"][0]


@pytest.mark.django_db
def test_cleanup_ecr_repos_falls_back_when_archive_kwarg_unsupported(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    """Older ECR drivers don't accept ``archive=`` — the activity
    falls back to a no-kwarg call rather than failing."""
    calls: list[tuple] = []

    def _delete_repo(name, **kwargs):
        # Reject the kwarg the first time so the fallback triggers.
        if kwargs:
            raise TypeError("unexpected kwarg 'archive'")
        calls.append((name,))

    _install_fake_driver(
        monkeypatch,
        capability="registry",
        methods={"delete_repo": _delete_repo},
    )
    summary = _cleanup_ecr_repos_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 1
    assert calls == [(app_on_cluster.slug,)]


# ---- cleanup_cluster_dns_records ----------------------------------


@pytest.mark.django_db
def test_cleanup_dns_records_walks_custom_domains_and_ingress_rules(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
    )
    IngressRule.objects.create(
        registered_app=app_on_cluster,
        subdomain="hello",
        hostname="hello.dev.astrolift.app",
        is_active=True,
    )
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="dns",
        methods={"delete_record": rec.record("delete_record")},
    )
    summary = _cleanup_dns_records_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 2
    # Records sent: (zone, name, type) for each.
    splits = sorted((args[0], args[1], args[2]) for _, args, _ in rec.calls)
    assert splits == sorted(
        [
            ("acme.com", "api", "CNAME"),
            ("dev.astrolift.app", "hello", "CNAME"),
        ],
    )


@pytest.mark.django_db
def test_cleanup_dns_records_skipped_on_unimplemented_driver(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.PENDING,
    )
    _install_unimplemented_driver(monkeypatch, capability="dns")
    summary = _cleanup_dns_records_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["skipped"] is True


@pytest.mark.django_db
def test_cleanup_dns_records_collects_per_target_errors(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="ok.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
    )
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="bad.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
    )

    def _delete(zone, name, record_type):
        if name == "bad":
            raise RuntimeError("route53 throttled")

    _install_fake_driver(
        monkeypatch,
        capability="dns",
        methods={"delete_record": _delete},
    )
    summary = _cleanup_dns_records_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 1
    assert len(summary["errors"]) == 1
    assert "throttled" in summary["errors"][0]


@pytest.mark.django_db
def test_cleanup_dns_records_no_apps_returns_zero(cluster, monkeypatch):
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="dns",
        methods={"delete_record": rec.record("delete_record")},
    )
    summary = _cleanup_dns_records_sync(cluster.pk)
    assert summary["deleted"] == 0
    assert rec.calls == []


# ---- cleanup_cluster_acm_certs ------------------------------------


@pytest.mark.django_db
def test_cleanup_acm_certs_revokes_each_cert_id(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        certificate_id="acm-arn-1",
    )
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="checkout.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        certificate_id="acm-arn-2",
    )
    # Domain with no cert_id — must be skipped silently.
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="byo.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.BYO,
        certificate_id="",
    )
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="tls",
        methods={"revoke_certificate": rec.record("revoke_certificate")},
    )
    summary = _cleanup_acm_certs_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 2
    revoked = sorted(args[0] for _, args, _ in rec.calls)
    assert revoked == ["acm-arn-1", "acm-arn-2"]


@pytest.mark.django_db
def test_cleanup_acm_certs_skipped_on_unimplemented_driver(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_id="acm-arn-1",
    )
    _install_unimplemented_driver(monkeypatch, capability="tls")
    summary = _cleanup_acm_certs_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["skipped"] is True


@pytest.mark.django_db
def test_cleanup_acm_certs_collects_per_target_errors(
    app_on_cluster,
    cluster,
    monkeypatch,
):
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="ok.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_id="acm-arn-good",
    )
    CustomDomain.objects.create(
        registered_app=app_on_cluster,
        hostname="bad.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_id="acm-arn-bad",
    )

    def _revoke(cert_id):
        if cert_id == "acm-arn-bad":
            raise RuntimeError("acm in use")

    _install_fake_driver(
        monkeypatch,
        capability="tls",
        methods={"revoke_certificate": _revoke},
    )
    summary = _cleanup_acm_certs_sync(cluster.pk)
    assert summary["ok"] is True
    assert summary["deleted"] == 1
    assert len(summary["errors"]) == 1
    assert "in use" in summary["errors"][0]


# ---- Workflow orchestration: activity scheduling order ------------


@pytest.mark.django_db
def test_workflow_schedules_cleanup_activities_before_teardown(
    cluster,
    monkeypatch,
):
    """The workflow body must call DNS → certs → ECR → IRSA in order
    BEFORE ``teardown_cluster_infra`` and ``mark_decommissioned``.

    We don't spin up a Temporal worker — the orchestrator is plain
    ``async`` code that calls ``workflow.execute_activity(...)`` so we
    can patch the temporalio.workflow surface and run ``run`` against
    an in-process recorder. Mirrors the reasoning in
    ``test_bring_cluster_into_management.py``: the orchestration
    logic is straight-line dispatch and a real worker fixture would
    require ``transaction=True`` (per the project's saved gotcha).
    """
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        cleanup_cluster_acm_certs,
        cleanup_cluster_dns_records,
        cleanup_cluster_ecr_repos,
        cleanup_cluster_irsa_roles,
        ensure_cluster_drained,
        mark_decommissioned,
        mark_decommissioning,
        remove_platform_rbac,
        teardown_cluster_infra,
    )
    from astrolift_workflows.inputs import Actor, DecommissionClusterInput
    from astrolift_workflows.workflows.decommission_cluster import (
        DecommissionClusterWorkflow,
    )

    schedule_log: list[str] = []
    activity_returns: dict[Any, Any] = {
        # Cleanup activities each return a benign report — workflow
        # folds them into ``data``.
        cleanup_cluster_dns_records: {"ok": True, "deleted": 1, "errors": [], "skipped": False},
        cleanup_cluster_acm_certs: {"ok": True, "deleted": 0, "errors": [], "skipped": False},
        cleanup_cluster_ecr_repos: {"ok": True, "deleted": 2, "errors": [], "skipped": False},
        cleanup_cluster_irsa_roles: {"ok": True, "deleted": 1, "errors": [], "skipped": False},
        teardown_cluster_infra: {"success": True, "deleted": ["cluster"], "skipped": [], "messages": []},
    }

    async def _fake_execute_activity(activity_fn, *args, **kwargs):
        # The workflow uses both positional-arg and ``args=[...]`` forms.
        schedule_log.append(getattr(activity_fn, "__name__", str(activity_fn)))
        # Return a per-activity payload so the workflow's downstream
        # consumers (e.g., teardown's deleted-list extraction) see a
        # well-shaped dict. Default to None for state-flip activities.
        return activity_returns.get(activity_fn)

    # Patch the temporalio.workflow attributes the workflow body uses
    # so we can drive ``run`` outside a real Temporal worker.
    monkeypatch.setattr(temporalio_workflow, "execute_activity", _fake_execute_activity)

    class _NullLogger:
        def warning(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

    monkeypatch.setattr(temporalio_workflow, "logger", _NullLogger())

    workflow_instance = DecommissionClusterWorkflow()
    actor = Actor(kind="user", user_id=1, display="tester")
    input_payload = DecommissionClusterInput(
        cluster_id=cluster.pk,
        actor=actor,
        delete_cloud_infra=True,
    )
    result = asyncio.new_event_loop().run_until_complete(
        workflow_instance.run(input_payload),
    )
    assert result.ok is True
    # Spec'd order: drain → mark_decommissioning → remove_platform_rbac
    # → cleanup_dns → cleanup_acm → cleanup_ecr → cleanup_irsa
    # → teardown_cluster_infra → mark_decommissioned.
    expected = [
        ensure_cluster_drained.__name__,
        mark_decommissioning.__name__,
        remove_platform_rbac.__name__,
        cleanup_cluster_dns_records.__name__,
        cleanup_cluster_acm_certs.__name__,
        cleanup_cluster_ecr_repos.__name__,
        cleanup_cluster_irsa_roles.__name__,
        teardown_cluster_infra.__name__,
        mark_decommissioned.__name__,
    ]
    assert schedule_log == expected
    # Cleanup summary surfaces in the workflow result data.
    assert result.data is not None
    assert set(result.data["cleanup"].keys()) == {
        "dns_records",
        "acm_certs",
        "ecr_repos",
        "irsa_roles",
    }


@pytest.mark.django_db
def test_workflow_continues_when_cleanup_activity_raises(
    cluster,
    monkeypatch,
):
    """Best-effort guarantee: if a cleanup activity exhausts retries
    and raises, the workflow records the failure into ``data`` and
    proceeds to the next phase rather than aborting."""
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        cleanup_cluster_acm_certs,
        cleanup_cluster_dns_records,
        cleanup_cluster_ecr_repos,
        cleanup_cluster_irsa_roles,
        teardown_cluster_infra,
    )
    from astrolift_workflows.inputs import Actor, DecommissionClusterInput
    from astrolift_workflows.workflows.decommission_cluster import (
        DecommissionClusterWorkflow,
    )

    schedule_log: list[str] = []
    benign = {"ok": True, "deleted": 0, "errors": [], "skipped": False}

    async def _fake_execute_activity(activity_fn, *args, **kwargs):
        schedule_log.append(getattr(activity_fn, "__name__", str(activity_fn)))
        if activity_fn is cleanup_cluster_ecr_repos:
            raise RuntimeError("ecr api down")
        if activity_fn in (
            cleanup_cluster_dns_records,
            cleanup_cluster_acm_certs,
            cleanup_cluster_irsa_roles,
        ):
            return benign
        if activity_fn is teardown_cluster_infra:
            return {"success": True, "deleted": [], "skipped": [], "messages": []}
        return None

    class _NullLogger:
        def warning(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

    monkeypatch.setattr(temporalio_workflow, "execute_activity", _fake_execute_activity)
    monkeypatch.setattr(temporalio_workflow, "logger", _NullLogger())

    workflow_instance = DecommissionClusterWorkflow()
    input_payload = DecommissionClusterInput(
        cluster_id=cluster.pk,
        actor=Actor(kind="user", user_id=1, display="tester"),
        delete_cloud_infra=False,
    )
    result = asyncio.new_event_loop().run_until_complete(
        workflow_instance.run(input_payload),
    )
    # Workflow still reports ok=True (best-effort cleanup) but the
    # message carries a warning the operator can act on.
    assert result.ok is True
    assert "warning" in result.message
    # Both the failed and the subsequent activity were attempted —
    # the cleanup phase doesn't short-circuit on a single failure.
    assert cleanup_cluster_ecr_repos.__name__ in schedule_log
    assert cleanup_cluster_irsa_roles.__name__ in schedule_log
    # And the per-capability detail surfaces in data.
    assert result.data["cleanup"]["ecr_repos"]["ok"] is False
    assert any("ecr api down" in e for e in result.data["cleanup"]["ecr_repos"]["errors"])
