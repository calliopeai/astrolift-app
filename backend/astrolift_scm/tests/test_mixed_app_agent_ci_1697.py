"""An app that contains an agent gets the app workflow, not the agent one (#1697).

``_is_agent_app`` decided by "has an agent workload anywhere", which is a
different question from "is an agent package". An app whose manifest
declares ``web`` + ``refresh`` + an agent therefore got the agent
validator as its managed workflow -- and no deploy workflow at all, which
is the one thing an app repo cannot do without.

Agent discovery already draws the line in the right place: an agent
manifest declares exactly one workload, of kind ``agent``; a mixed
manifest is an app that happens to contain an agent. The agent workload
still dispatches -- it is a workload under the app -- it just is not a
separately registered agent package.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_scm.services import workflow_sync
from astrolift_scm.services.workflow_sync import (
    _is_agent_app,
    _remove_superseded_workflow,
    _superseded_workflow_path,
    github_workflow_path_for,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def app(db):
    org = Organization.objects.create(name="Acme", slug="acme-1697")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1697")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-1697")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="EMR",
        slug="emr-1697",
        source_repo="acme/emr",
        provisioning_status="ready",
    )


def _workload(app, slug, kind):
    return Workload.objects.create(registered_app=app, name=slug, slug=slug, kind=kind)


def test_an_agent_only_app_is_an_agent_package(app):
    _workload(app, "brief", Workload.Kind.AGENT)

    assert _is_agent_app(app) is True
    assert github_workflow_path_for(app) == ".github/workflows/astrolift-agent-emr-1697.yml"


def test_an_app_with_an_agent_among_its_workloads_is_an_app(app):
    _workload(app, "web", Workload.Kind.DEPLOYMENT)
    _workload(app, "refresh", Workload.Kind.CRONJOB)
    _workload(app, "brief", Workload.Kind.AGENT)

    assert _is_agent_app(app) is False
    assert github_workflow_path_for(app) == f".github/workflows/astrolift-app-{app.guid.hex}.yml"


def test_an_app_with_no_agent_is_an_app(app):
    _workload(app, "web", Workload.Kind.DEPLOYMENT)

    assert _is_agent_app(app) is False


def test_a_soft_deleted_app_workload_does_not_keep_an_agent_from_being_one(app):
    """Deregistering the app half of a mixed repo leaves the agent."""
    from django.utils import timezone

    web = _workload(app, "web", Workload.Kind.DEPLOYMENT)
    _workload(app, "brief", Workload.Kind.AGENT)
    web.deleted_at = timezone.now()
    web.save(update_fields=["deleted_at"])

    assert _is_agent_app(app) is True


def test_the_superseded_path_is_the_other_one(app):
    """Whichever workflow the app uses now, the other one is the file that
    keeps running and keeps failing until it is removed."""
    _workload(app, "brief", Workload.Kind.AGENT)
    assert _superseded_workflow_path(app) == f".github/workflows/astrolift-app-{app.guid.hex}.yml"

    _workload(app, "web", Workload.Kind.DEPLOYMENT)
    assert _superseded_workflow_path(app) == ".github/workflows/astrolift-agent-emr-1697.yml"


# ---------------------------------------------------------------------
# removing the file the app no longer uses
# ---------------------------------------------------------------------


_STAMPED = "# managed\n# astrolift-managed: template-version=7 sha256=" + ("0" * 64) + "\nname: old\n"
_OPERATOR_AUTHORED = "# my own workflow\nname: mine\n"


@pytest.fixture
def deletes(monkeypatch):
    """Capture delete calls instead of reaching GitHub."""
    calls: list[dict] = []

    def _delete(connection, **kw):
        calls.append(kw)
        return True

    monkeypatch.setattr(
        "astrolift_scm.providers.github.delete_github_file",
        _delete,
    )
    return calls


def _serve(monkeypatch, body):
    monkeypatch.setattr(workflow_sync, "fetch_file", lambda *a, **kw: body)


def test_a_legacy_stamped_superseded_workflow_is_retained(app, monkeypatch, deletes):
    _workload(app, "web", Workload.Kind.DEPLOYMENT)
    _workload(app, "brief", Workload.Kind.AGENT)
    _serve(monkeypatch, _STAMPED)

    removed = _remove_superseded_workflow(object(), app, branch="main")

    assert removed is None
    assert deletes == []


def test_an_operator_authored_file_at_that_path_is_left_alone(app, monkeypatch, deletes):
    """No stamp means the operator wrote it. Not ours to delete, however
    inconvenient it is."""
    _workload(app, "web", Workload.Kind.DEPLOYMENT)
    _workload(app, "brief", Workload.Kind.AGENT)
    _serve(monkeypatch, _OPERATOR_AUTHORED)

    assert _remove_superseded_workflow(object(), app, branch="main") is None
    assert deletes == []


def test_nothing_at_the_old_path_is_not_an_error(app, monkeypatch, deletes):
    _workload(app, "web", Workload.Kind.DEPLOYMENT)
    _serve(monkeypatch, None)

    assert _remove_superseded_workflow(object(), app, branch="main") is None
    assert deletes == []


def test_a_failed_cleanup_does_not_fail_the_sync(app, monkeypatch):
    """The stale file is a nuisance; the sync that just succeeded is the
    point."""
    _workload(app, "web", Workload.Kind.DEPLOYMENT)
    _workload(app, "brief", Workload.Kind.AGENT)
    _serve(monkeypatch, workflow_sync.render_astrolift_agent_ci_workflow(app))

    def _boom(connection, **kw):
        raise RuntimeError("github said no")

    monkeypatch.setattr("astrolift_scm.providers.github.delete_github_file", _boom)

    assert _remove_superseded_workflow(object(), app, branch="main") is None
