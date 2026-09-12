"""A registration-time bootstrap failure has to stop being reported once
the repo has fixed it (#1692).

``manifest_bootstrap_status`` records why an app registered without its
workloads, and the app detail page renders it as "This app registered
without its workloads ... no services were created from it", plus
whatever error was captured -- often "no active source connection found
for this organization". It was written once, at registration, and never
revisited. An app whose manifest was fixed and resynced, with its
workloads materialised and a working GitHub App connection, went on
telling the operator it had neither.

The banner is a current-state signal. A resync that applies is the event
that makes the registration failure historical, so that is where it is
retired.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.services.manifest_sync import resync_app_manifest_from_repo
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError

pytestmark = pytest.mark.django_db

_TOML = """\
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
replicas = 2

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _app(*, bootstrap_status: str, bootstrap_error: str = "", manifest_raw: str = ""):
    org = Organization.objects.create(name="Acme", slug="acme-1692")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1692")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-1692")
    SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_PAT,
        display_name="Acme PAT",
        account_login="acme",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-1692",
        provisioning_status="ready",
        source_kind="github",
        source_repo="acme/hello",
        manifest_path="astrolift.toml",
        deploy_branch="main",
        default_branch="main",
        manifest_raw=manifest_raw,
        manifest_bootstrap_status=bootstrap_status,
        manifest_bootstrap_error=bootstrap_error,
    )
    if manifest_raw.strip():
        from astrolift_manifest.normalize import NormalizationDefaults, normalize
        from astrolift_manifest.parser import parse_raw
        from astrolift_manifest.persist import persist_manifest

        persist_manifest(
            app,
            normalize(parse_raw(manifest_raw), defaults=NormalizationDefaults()),
            raw_text=manifest_raw,
        )
        app.refresh_from_db()
    return app


def _fetch(text):
    def _f(connection, repo_full_name, path, ref):  # noqa: ARG001
        return text

    return _f


def _raises(exc):
    def _f(connection, repo_full_name, path, ref):  # noqa: ARG001
        raise exc

    return _f


def test_an_applying_resync_retires_the_failure():
    """The bug: the banner outlived the condition it described."""

    app = _app(
        bootstrap_status="fetch_failed",
        bootstrap_error="no active source connection found for this organization",
    )

    result = resync_app_manifest_from_repo(app, fetch=_fetch(_TOML))

    assert result.status == "applied"
    app.refresh_from_db()
    assert app.manifest_bootstrap_status == "applied"
    assert app.manifest_bootstrap_error == ""
    # And the thing the banner claimed did not exist now does.
    assert Workload.objects.filter(registered_app=app, deleted_at__isnull=True).exists()


def test_an_in_sync_resync_retires_it_too():
    """Repo already matches the DB: the workloads are there either way,
    so the banner is just as wrong."""

    app = _app(
        bootstrap_status="parse_failed",
        bootstrap_error="expected a table",
        manifest_raw=_TOML,
    )

    result = resync_app_manifest_from_repo(app, fetch=_fetch(_TOML))

    assert result.status == "in_sync"
    app.refresh_from_db()
    assert app.manifest_bootstrap_status == "applied"
    assert app.manifest_bootstrap_error == ""


def test_a_failed_resync_leaves_the_failure_standing():
    """Nothing was fixed, so nothing is retired."""

    app = _app(bootstrap_status="fetch_failed", bootstrap_error="boom")

    result = resync_app_manifest_from_repo(
        app, fetch=_raises(ProviderError(code="FETCH_FAILED", message="still broken"))
    )

    assert result.status == "fetch_failed"
    app.refresh_from_db()
    assert app.manifest_bootstrap_status == "fetch_failed"
    assert app.manifest_bootstrap_error == "boom"


def test_a_diverged_resync_leaves_the_failure_standing():
    """A staged draft blocked the apply, so the workloads are still
    whatever registration left behind."""

    app = _app(bootstrap_status="fetch_failed", bootstrap_error="boom")
    app.manifest_raw_staged = 'name = "staged"\n'
    app.save(update_fields=["manifest_raw_staged"])

    result = resync_app_manifest_from_repo(app, fetch=_fetch(_TOML))

    assert result.status == "diverged"
    app.refresh_from_db()
    assert app.manifest_bootstrap_status == "fetch_failed"


def test_a_healthy_app_is_left_alone():
    """No write, no version bump, for the overwhelmingly common case."""

    app = _app(bootstrap_status="applied", manifest_raw=_TOML)
    before = app.version

    resync_app_manifest_from_repo(app, fetch=_fetch(_TOML))

    app.refresh_from_db()
    assert app.manifest_bootstrap_status == "applied"
    # in_sync still stamps last_resync_at, so the row does move -- what
    # matters is that the bootstrap columns were not part of the write.
    assert app.version >= before
