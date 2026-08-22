"""
run_app_doctor (#1550) — checks name real states, never raise.

Pins the report contract: manifest drift/parse failures carry the resync
fix handle; missing registry/push-role provisioning fails with the right
remedy; DNS passes only on RESOLUTION (a record that answers nothing is
a fail, #1534); stranded in-flight deployments warn (#1536); and a
crashed probe reports unknown instead of sinking the report.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_registry.services.app_doctor import (
    CHECK_DEPLOYMENTS,
    CHECK_DNS,
    CHECK_MANIFEST,
    CHECK_PUSH_ROLE,
    CHECK_REGISTRY,
    run_app_doctor,
)

pytestmark = pytest.mark.django_db


def _by_key(report):
    return {c.key: c for c in report.checks}


class _Result:
    def __init__(self, status, error=""):
        self.status = status
        self.error = error


@pytest.fixture
def in_sync_manifest(monkeypatch):
    import astrolift_registry.services.manifest_sync as manifest_sync

    monkeypatch.setattr(manifest_sync, "resync_app_manifest_from_repo", lambda a, **k: _Result("in_sync"))


def test_healthy_app_passes(app, env, in_sync_manifest):
    app.source_repo = "acme/api"
    app.registry_repo_uri = "123.dkr.ecr.us-west-2.amazonaws.com/acme/api"
    app.push_role_ref = "arn:aws:iam::123:role/astrolift-acme-api-ecr-push"
    app.autowire_state = {"webhook": {"status": "ok"}, "workflow": {"status": "ok"}}
    app.save()
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
    )

    report = run_app_doctor(app, resolve=lambda host: ["1.2.3.4"])

    checks = _by_key(report)
    assert checks[CHECK_MANIFEST].status == "pass"
    assert checks[CHECK_REGISTRY].status == "pass"
    assert checks[CHECK_PUSH_ROLE].status == "pass"
    assert checks[CHECK_DEPLOYMENTS].status == "pass"
    assert report.healthy or any(c.status == "warn" for c in report.checks)


def test_manifest_parse_failure_names_resync_fix(app, monkeypatch):
    import astrolift_registry.services.manifest_sync as manifest_sync

    app.source_repo = "acme/api"
    app.save()
    monkeypatch.setattr(
        manifest_sync,
        "resync_app_manifest_from_repo",
        lambda a, **k: _Result("fetch_failed", "repo manifest failed to parse: name missing"),
    )

    check = _by_key(run_app_doctor(app, resolve=lambda h: []))[CHECK_MANIFEST]
    assert check.status == "fail"
    assert check.fix == "resync_manifest"
    assert "parse" in check.detail


def test_missing_provisioning_fails_with_remedies(app, in_sync_manifest):
    app.source_repo = "acme/api"
    app.registry_repo_uri = ""
    app.push_role_ref = ""
    app.save()

    checks = _by_key(run_app_doctor(app, resolve=lambda h: []))
    assert checks[CHECK_REGISTRY].status == "fail"
    assert checks[CHECK_REGISTRY].fix == "rerun_onboarding"
    assert checks[CHECK_PUSH_ROLE].status == "fail"
    assert checks[CHECK_PUSH_ROLE].fix == "retry_autowire"


def test_dns_requires_resolution_not_record_existence(app, in_sync_manifest, monkeypatch):
    import astrolift_registry.services.app_doctor as doctor

    monkeypatch.setattr(doctor, "_public_hostnames", lambda a: ["app.example.test"])

    dead = _by_key(run_app_doctor(app, resolve=lambda host: []))[CHECK_DNS]
    assert dead.status == "fail"
    assert "answer" in dead.detail

    alive = _by_key(run_app_doctor(app, resolve=lambda host: ["1.2.3.4"]))[CHECK_DNS]
    assert alive.status == "pass"


def test_stranded_in_flight_rows_warn(app, env, in_sync_manifest):
    for _ in range(2):
        Deployment.objects.create(
            registered_app=app,
            app_environment=env,
            trigger_kind=Deployment.TriggerKind.MANUAL.value,
            status=Deployment.Status.DEPLOYING.value,
            image_tag="v1",
        )

    check = _by_key(run_app_doctor(app, resolve=lambda h: []))[CHECK_DEPLOYMENTS]
    assert check.status == "warn"
    assert "#1536" in check.detail


def test_crashed_probe_reports_unknown_not_raise(app, monkeypatch):
    import astrolift_registry.services.manifest_sync as manifest_sync

    app.source_repo = "acme/api"
    app.save()

    def _boom(a, **k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(manifest_sync, "resync_app_manifest_from_repo", _boom)

    check = _by_key(run_app_doctor(app, resolve=lambda h: []))[CHECK_MANIFEST]
    assert check.status == "unknown"
