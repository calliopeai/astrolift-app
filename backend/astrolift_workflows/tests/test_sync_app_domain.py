"""SyncAppDomainWorkflow — the wiring of the #143 sync policy.

``sync_domain.py`` held the whole policy (step order, hostname diff,
wildcard-cert coverage, rollback plan) and nothing called it:
``setAppSubdomain`` wrote the row, reported success, and left DNS, the
Ingress host rule and the cert pointing at the old name. These tests pin
the three pieces that close that gap — the plan the workflow reads, the
step set the policy produces, and the revert that rollback stands on —
plus the registration without which the workflow could never run.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_lifecycle.models import Deployment
from astrolift_workflows.activities.app_domain_sync import (
    _plan_app_domain_sync_sync,
    _revert_app_subdomain_sync,
    _verify_app_hostnames_sync,
)
from astrolift_workflows.sync_domain import SYNC_ORDER, SyncStep
from astrolift_workflows.workflows.sync_app_domain import (
    SyncAppDomainWorkflow,
    plan_steps,
)

_MANIFEST = """name = "hello-app"
[[workloads]]
name = "web"
kind = "deployment"
is_public = true
replicas = 1
  [[workloads.containers]]
  name = "app"
  is_primary = true
  image_ref = "docker.io/calliopeai/sample:main"
  port = 8080
"""


# ---- step planning (pure policy binding) ----------------------------


def test_no_hostname_change_plans_no_steps():
    """An env whose hostnames didn't move must not have its routing
    touched — otherwise a rename in one env re-applies every other."""
    steps, added = plan_steps(
        old_hostnames=["hello.apps.example.net"],
        new_hostnames=["hello.apps.example.net"],
        wildcard_sans=["*.apps.example.net"],
    )
    assert steps == ()
    assert added == ()


def test_wildcard_covered_rename_skips_the_cert_request():
    """The whole point of the coverage check: ``*.apps.example.net``
    already covers ``hello-v2.apps.example.net``, so no ACME round-trip."""
    steps, added = plan_steps(
        old_hostnames=["hello.apps.example.net"],
        new_hostnames=["hello-v2.apps.example.net"],
        wildcard_sans=["*.apps.example.net"],
    )
    assert added == ("hello-v2.apps.example.net",)
    assert SyncStep.REQUEST_CERT not in steps
    assert steps == tuple(s for s in SYNC_ORDER if s != SyncStep.REQUEST_CERT)


def test_uncovered_hostname_keeps_the_cert_request():
    """No wildcard on the zone means the new host has no cert; the step
    stays in, in its spec'd position."""
    steps, added = plan_steps(
        old_hostnames=["hello.apps.example.net"],
        new_hostnames=["hello-v2.apps.example.net"],
        wildcard_sans=[],
    )
    assert added == ("hello-v2.apps.example.net",)
    assert steps == SYNC_ORDER


def test_steps_follow_spec_order():
    steps, _ = plan_steps(
        old_hostnames=["a.apps.example.net"],
        new_hostnames=["b.apps.example.net"],
        wildcard_sans=[],
    )
    assert steps[0] == SyncStep.UPDATE_DNS
    assert steps[1] == SyncStep.PATCH_INGRESS
    assert steps[-1] == SyncStep.VERIFY_E2E


# ---- registration ---------------------------------------------------


def test_workflow_and_activities_are_registered():
    """Started by name from the mutation; unregistered means the task
    sits with no worker polling for it (the PipelineRunWorkflow bug)."""
    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS

    assert SyncAppDomainWorkflow in WORKFLOWS
    served = {getattr(a, "__name__", "") for a in ACTIVITIES}
    assert {
        "plan_app_domain_sync",
        "revert_app_subdomain",
        "verify_app_hostnames",
        "ensure_static_dns",
        "apply_manifests",
        "request_wildcard_cert_for_zone",
        "wait_dns",
    } <= served


# ---- plan activity --------------------------------------------------


@pytest.fixture
def managed_domain():
    return ManagedDomain.objects.create(
        zone="apps.example.net",
        dns_driver="test-provider",
        dns_config={"zone_id": "Z123", "certificate_arn": "arn:acm:wildcard"},
        is_wildcard_managed=True,
    )


@pytest.fixture
def live_app(app, env, managed_domain):
    app.manifest_raw = _MANIFEST
    app.subdomain = "hello-v2"
    app.save(update_fields=["manifest_raw", "subdomain", "updated_at", "version"])
    env.managed_domain = managed_domain
    env.save(update_fields=["managed_domain", "updated_at", "version"])
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING,
    )
    return app


@pytest.mark.django_db
def test_plan_returns_old_and_new_hostnames(live_app, env):
    plan = _plan_app_domain_sync_sync(live_app.pk, "hello")
    assert len(plan["plans"]) == 1
    p = plan["plans"][0]
    assert p["app_environment_id"] == env.pk
    assert p["old_hostnames"] == ["hello.apps.example.net"]
    assert p["new_hostnames"] == ["hello-v2.apps.example.net"]
    assert p["zone"] == "apps.example.net"
    assert p["zone_id"] == "Z123"
    assert p["wildcard_sans"] == ["*.apps.example.net"]


@pytest.mark.django_db
def test_plan_skips_env_without_a_live_rollout(live_app, env):
    """No RUNNING deployment means there is no Ingress to patch; the
    next deploy renders the new host anyway."""
    for deployment in Deployment.objects.filter(app_environment=env):
        deployment.soft_delete()
    assert _plan_app_domain_sync_sync(live_app.pk, "hello")["plans"] == []


@pytest.mark.django_db
def test_plan_skips_env_without_a_managed_domain(live_app, env):
    env.managed_domain = None
    env.save(update_fields=["managed_domain", "updated_at", "version"])
    assert _plan_app_domain_sync_sync(live_app.pk, "hello")["plans"] == []


@pytest.mark.django_db
def test_plan_reports_no_wildcard_when_zone_has_no_cert(live_app, managed_domain):
    managed_domain.is_wildcard_managed = False
    managed_domain.dns_config = {"zone_id": "Z123"}
    managed_domain.provision_cert_id = ""
    managed_domain.save(
        update_fields=[
            "is_wildcard_managed",
            "dns_config",
            "provision_cert_id",
            "updated_at",
            "version",
        ]
    )
    plan = _plan_app_domain_sync_sync(live_app.pk, "hello")
    assert plan["plans"][0]["wildcard_sans"] == []


# ---- rollback anchor ------------------------------------------------


@pytest.mark.django_db
def test_revert_puts_the_previous_subdomain_back(live_app):
    """Renderers derive the hostname from this field, so the revert is
    what makes the replayed apply restore the old routing."""
    assert _revert_app_subdomain_sync(live_app.pk, "hello") == "hello"
    live_app.refresh_from_db()
    assert live_app.subdomain == "hello"


# ---- verification ---------------------------------------------------


def test_verify_reports_unverified_hostnames(monkeypatch):
    def _boom(hostname):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr(
        "astrolift_workflows.activities.app_domain_sync._tls_handshake",
        _boom,
    )
    out = _verify_app_hostnames_sync(["hello-v2.apps.example.net"], 1)
    assert out["verified"] == []
    assert out["unverified"] == ["hello-v2.apps.example.net"]
    assert "ConnectionRefusedError" in out["errors"]["hello-v2.apps.example.net"]


def test_verify_passes_when_tls_terminates(monkeypatch):
    monkeypatch.setattr(
        "astrolift_workflows.activities.app_domain_sync._tls_handshake",
        lambda hostname: None,
    )
    out = _verify_app_hostnames_sync(["hello-v2.apps.example.net"], 1)
    assert out["verified"] == ["hello-v2.apps.example.net"]
    assert out["unverified"] == []
