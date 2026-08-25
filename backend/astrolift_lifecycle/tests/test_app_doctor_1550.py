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

    report = run_app_doctor(app)

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

    check = _by_key(run_app_doctor(app))[CHECK_MANIFEST]
    assert check.status == "fail"
    assert check.fix == "resync_manifest"
    assert "parse" in check.detail


def test_missing_provisioning_fails_with_remedies(app, in_sync_manifest):
    app.source_repo = "acme/api"
    app.registry_repo_uri = ""
    app.push_role_ref = ""
    app.save()

    checks = _by_key(run_app_doctor(app))
    assert checks[CHECK_REGISTRY].status == "fail"
    assert checks[CHECK_REGISTRY].fix == "rerun_onboarding"
    assert checks[CHECK_PUSH_ROLE].status == "fail"
    assert checks[CHECK_PUSH_ROLE].fix == "retry_autowire"


def test_dns_requires_resolution_not_record_existence(app, in_sync_manifest, monkeypatch):
    """Unchanged in substance, moved in mechanism (#1550).

    The doctor reads a cached probe now instead of resolving live, so the
    resolver is injected into `probe_app_dns` rather than into the report.
    What is asserted is the same thing #1534 was about: a hostname that
    answers nothing fails, and record-existence is not resolution.
    """
    import astrolift_registry.services.app_doctor as doctor
    from astrolift_registry.services.app_doctor import probe_app_dns

    monkeypatch.setattr(doctor, "_public_hostnames", lambda a: ["app.example.test"])

    probe_app_dns(app, resolve=lambda host: [])
    dead = _by_key(run_app_doctor(app))[CHECK_DNS]
    assert dead.status == "fail"
    assert "answer" in dead.detail

    probe_app_dns(app, resolve=lambda host: ["1.2.3.4"])
    alive = _by_key(run_app_doctor(app))[CHECK_DNS]
    assert alive.status == "pass"


def test_an_unprobed_app_is_unknown_not_pass(app, in_sync_manifest, monkeypatch):
    """The property the cache introduces, and the one worth guarding: "nobody
    has checked" and "it resolves" are different answers."""
    import astrolift_registry.services.app_doctor as doctor

    monkeypatch.setattr(doctor, "_public_hostnames", lambda a: ["app.example.test"])

    assert _by_key(run_app_doctor(app))[CHECK_DNS].status == "unknown"


def test_a_stale_probe_is_unknown_not_pass(app, in_sync_manifest, monkeypatch):
    """A record that resolved a week ago and has not been re-probed is not
    evidence that it resolves now."""
    import datetime as dt

    import astrolift_registry.services.app_doctor as doctor

    monkeypatch.setattr(doctor, "_public_hostnames", lambda a: ["app.example.test"])
    app.dns_probe = {
        "probed_at": (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat(),
        "unresolved": [],
    }
    app.save(update_fields=["dns_probe"])

    check = _by_key(run_app_doctor(app))[CHECK_DNS]
    assert check.status == "unknown"
    assert "too old" in check.detail


def test_a_probe_that_could_not_resolve_records_the_host_as_dead(app, monkeypatch):
    """A resolver error is not a pass. Recording it as unresolved is the
    conservative direction -- a failed lookup has not established the name
    works, and treating it as fine is the #1534 failure one level up."""
    import astrolift_registry.services.app_doctor as doctor
    from astrolift_registry.services.app_doctor import probe_app_dns

    monkeypatch.setattr(doctor, "_public_hostnames", lambda a: ["app.example.test"])

    def _boom(host):
        raise OSError("resolver unreachable")

    record = probe_app_dns(app, resolve=_boom)

    assert record["unresolved"] == ["app.example.test"]


def test_the_report_makes_no_network_call(app, in_sync_manifest, monkeypatch):
    """The whole point of the cache. This renders on every app-detail load."""
    import socket

    import astrolift_registry.services.app_doctor as doctor

    monkeypatch.setattr(doctor, "_public_hostnames", lambda a: ["app.example.test"])

    def _forbidden(*args, **kwargs):
        raise AssertionError("the doctor resolved a hostname on a page load")

    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)

    run_app_doctor(app)


def test_stranded_in_flight_rows_warn(app, env, in_sync_manifest):
    for _ in range(2):
        Deployment.objects.create(
            registered_app=app,
            app_environment=env,
            trigger_kind=Deployment.TriggerKind.MANUAL.value,
            status=Deployment.Status.DEPLOYING.value,
            image_tag="v1",
        )

    check = _by_key(run_app_doctor(app))[CHECK_DEPLOYMENTS]
    assert check.status == "warn"
    assert "#1536" in check.detail


def test_crashed_probe_reports_unknown_not_raise(app, monkeypatch):
    import astrolift_registry.services.manifest_sync as manifest_sync

    app.source_repo = "acme/api"
    app.save()

    def _boom(a, **k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(manifest_sync, "resync_app_manifest_from_repo", _boom)

    check = _by_key(run_app_doctor(app))[CHECK_MANIFEST]
    assert check.status == "unknown"


def test_every_fix_is_a_machine_readable_verb():
    """The frontend switches on `fix` to pick a mutation, so a prose
    sentence there reaches its default branch and the button does nothing.

    I introduced four prose values adding the identity, image and cached-DNS
    checks (#1550) before noticing the pre-existing convention. This is the
    guard so the next person does not.

    Empty is allowed and is the honest answer when no mutation repairs the
    finding -- a missing cluster `account_id` is a cluster-config change, and
    an unprobed hostname resolves itself on the next sweep. The explanation
    goes in `detail`.
    """
    import inspect

    import astrolift_registry.services.app_doctor as doctor

    known = {"", "resync_manifest", "retry_autowire", "rerun_onboarding", "redeploy"}

    offenders = []
    for name, fn in vars(doctor).items():
        if not name.startswith("_check_") or not callable(fn):
            continue
        for line in inspect.getsource(fn).splitlines():
            stripped = line.strip()
            if not stripped.startswith("fix="):
                continue
            value = stripped[len("fix=") :].strip().rstrip(",").strip('"').strip("'")
            if value not in known:
                offenders.append(f"{name}: {value!r}")

    assert not offenders, (
        "these `fix` values are not verbs the frontend can map to a mutation:\n  "
        + "\n  ".join(offenders)
        + f"\n\nUse one of {sorted(known - {''})}, or empty with the explanation in `detail`."
    )
