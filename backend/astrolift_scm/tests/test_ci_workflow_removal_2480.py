"""Deregister removes the managed CI workflow it committed (#2480).

Real PostgreSQL rows and the real GitHub provider over HTTP, against the owned
receiver of the #2139 provider tests. Only a file this app provably wrote is
deleted, conditionally on its reviewed blob; anything else stays and is named.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from astrolift_registry.models import RegisteredApp, Workload
from astrolift_scm.ci_identity import github_ci_identity
from astrolift_scm.providers import ProviderError
from astrolift_scm.services import workflow_sync
from astrolift_scm.services.workflow_sync import (
    _superseded_workflow_path,
    github_workflow_path_for,
    remove_workflow_file_from_repo,
    render_astrolift_agent_ci_workflow,
    render_astrolift_ci_workflow,
    sync_workflow_file_to_repo,
)
from astrolift_scm.tests.test_ci_monorepo_2139 import apps as registered_apps
from astrolift_scm.tests.test_ci_monorepo_provider_2139 import host as github_host
from astrolift_workflows.activities.app_deregister import _remove_ci_workflow_sync

apps = registered_apps
host = github_host
pytestmark = pytest.mark.django_db

_LEGACY = ".github/workflows/astrolift-ci.yml"


def _review_branch(app) -> str:
    return f"astrolift/remove-ci-workflow-{github_ci_identity(app)}"


def _repo_writes(host) -> list[tuple[str, str]]:
    return [(method, route) for method, route in host.requests if method != "GET" and "/repos/" in route]


def test_deregister_removes_only_the_apps_own_workflow(apps, host):
    for app in apps:
        assert sync_workflow_file_to_repo(app).status == "created"
    gone, sibling = apps
    path = github_workflow_path_for(gone)
    sibling_file = host.files[github_workflow_path_for(sibling)]

    summary = remove_workflow_file_from_repo(gone)

    assert summary["status"] == "removed"
    assert summary["removed"] == [path] and summary["retained"] == [] and summary["pr_url"] == ""
    assert summary["detail"] == f"removed {path} from example/monorepo@main"
    # The receiver refuses a DELETE whose sha is not the current blob's.
    assert host.deletes == [path]
    assert host.files == {github_workflow_path_for(sibling): sibling_file}
    assert not host.pulls


def test_both_managed_shapes_of_the_app_are_removed(apps, host):
    """An app's workflow moves between the app and agent shapes (#1697); a
    cleanup that failed back then leaves both, and both are this app's."""
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    app_path = github_workflow_path_for(app)
    Workload.objects.create(registered_app=app, name="Agent", slug="agent", kind=Workload.Kind.AGENT)
    agent_path = github_workflow_path_for(app)
    assert agent_path != app_path
    host.files[agent_path] = render_astrolift_agent_ci_workflow(app)

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "removed"
    assert sorted(summary["removed"]) == sorted([app_path, agent_path])
    assert not host.files


def test_a_protected_deploy_branch_gets_a_review_pull_request(apps, host):
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    original = host.files[path]
    host.protected = True

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "pr_opened"
    assert summary["pr_url"] == "https://github.example.test/example/monorepo/pull/1"
    assert summary["removed"] == [path]
    assert host.files[path] == original
    assert path not in host.branches[_review_branch(app)]
    assert [(pull["head"], pull["base"]) for pull in host.pulls] == [(_review_branch(app), "main")]
    assert app.slug in host.pulls[0]["title"] and path in host.pulls[0]["body"]


def test_a_review_pull_request_whose_url_is_unknown_is_still_reported_as_opened(apps, host, monkeypatch):
    """GitHub answers a repeated pull request with "already exists", and the
    lookup of the open one can come back empty. The file is still on the
    deploy branch, so the summary must not call it removed."""
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    host.protected = True
    monkeypatch.setattr(workflow_sync, "_github_open_pull_request", lambda *args, **kwargs: "")

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "pr_opened" and summary["pr_url"] == ""
    assert summary["detail"] == (
        f"opened a pull request from {_review_branch(app)} to remove {path} from example/monorepo@main"
    )
    assert path in host.files


def test_a_retry_reuses_its_review_branch_without_a_second_delete(apps, host):
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    host.protected = True

    assert remove_workflow_file_from_repo(app)["status"] == "pr_opened"
    assert remove_workflow_file_from_repo(app)["status"] == "pr_opened"

    assert host.deletes == [path]
    assert path in host.files and path not in host.branches[_review_branch(app)]


def test_independent_edits_on_the_review_branch_are_refused(apps, host):
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    proposal = "# someone's proposal\nname: theirs\n"
    host.branches[_review_branch(app)] = {path: proposal}
    host.protected = True

    with pytest.raises(ProviderError) as refused:
        remove_workflow_file_from_repo(app)

    assert refused.value.code == "CONFLICT"
    assert f"delete {path} or {_superseded_workflow_path(app)} by hand" in refused.value.message
    assert host.branches[_review_branch(app)] == {path: proposal}
    assert not host.deletes and not host.pulls


@pytest.mark.parametrize("variant", ["edited", "another registration's", "legacy stamp", "operator's own"])
def test_a_file_this_app_cannot_prove_it_wrote_is_left_and_named(apps, host, variant):
    app, sibling = apps
    path = github_workflow_path_for(app)
    content = {
        "edited": render_astrolift_ci_workflow(app) + "# operator edit\n",
        "another registration's": render_astrolift_ci_workflow(sibling),
        "legacy stamp": "# astrolift-managed: template-version=7 sha256=" + "0" * 16 + "\nname: legacy\n",
        "operator's own": "name: mine\n",
    }[variant]
    host.files[path] = content

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "retained"
    assert summary["removed"] == [] and summary["retained"] == [path]
    assert path in summary["detail"] and "repository review" in summary["detail"]
    assert host.files[path] == content
    assert _repo_writes(host) == []


def test_a_protected_branch_with_nothing_removable_gets_no_review_branch(apps, host):
    """A review branch and pull request are only for a file this app can
    remove; an edited one on a protected branch is just named."""
    app = apps[0]
    path = github_workflow_path_for(app)
    edited = render_astrolift_ci_workflow(app) + "# operator edit\n"
    host.files[path] = edited
    host.protected = True

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "retained" and summary["retained"] == [path]
    assert host.files[path] == edited
    assert _repo_writes(host) == [] and set(host.branches) == {"main"}


def test_the_legacy_path_a_sync_recorded_is_named_never_deleted(apps, host):
    """Generations before 9 wrote one shared ``astrolift-ci.yml`` with no
    owner line; it can belong to every registration in the repository."""
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    app.ci_workflow_state = {**app.ci_workflow_state, "path": _LEGACY}
    app.save(update_fields=["ci_workflow_state"])
    legacy = "# astrolift-managed: template-version=8 sha256=" + "0" * 64 + "\nname: shared\n"
    host.files[_LEGACY] = legacy

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "removed"
    assert summary["removed"] == [github_workflow_path_for(app)] and summary["retained"] == [_LEGACY]
    assert host.files == {_LEGACY: legacy}


def test_nothing_to_remove_is_a_clean_no_op(apps, host):
    summary = remove_workflow_file_from_repo(apps[0])

    assert summary["status"] == "absent"
    assert summary["removed"] == [] and summary["retained"] == []
    assert summary["detail"] == "no managed CI workflow for this app on example/monorepo@main"
    assert _repo_writes(host) == []


def test_a_concurrent_edit_is_refused_and_the_failure_names_the_files(apps, host):
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    edited = host.files[path] + "# concurrent edit\n"

    def edit_after_review(branch, file, _current):
        if branch == "main" and file == path:
            host.after_read = None
            host.files[path] = edited

    host.after_read = edit_after_review

    with pytest.raises(ProviderError) as failed:
        remove_workflow_file_from_repo(app)

    assert failed.value.code == "DELETE_FAILED"
    assert "example/monorepo@main" in failed.value.message
    assert f"delete {path} or {_superseded_workflow_path(app)} by hand" in failed.value.message
    assert host.files[path] == edited and not host.deletes


def test_a_concurrent_edit_carried_onto_the_review_branch_is_refused(apps, host):
    """On a protected branch the review branch is cut from the deploy branch
    after the review read, so an edit landing in between rides along. The
    delete there is conditional on the reviewed blob too."""
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    edited = host.files[path] + "# concurrent edit\n"
    host.protected = True

    def edit_after_review(branch, file, _current):
        if branch == "main" and file == path:
            host.after_read = None
            host.files[path] = edited

    host.after_read = edit_after_review

    with pytest.raises(ProviderError) as failed:
        remove_workflow_file_from_repo(app)

    assert failed.value.code == "DELETE_FAILED"
    assert f"delete {path} or {_superseded_workflow_path(app)} by hand" in failed.value.message
    assert host.branches[_review_branch(app)][path] == edited
    assert host.files[path] == edited
    assert not host.deletes and not host.pulls


def test_without_a_connection_the_paths_are_named_for_manual_removal(apps):
    app = apps[0]

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "no_connection"
    assert summary["removed"] == [] and summary["retained"] == []
    assert f"delete {github_workflow_path_for(app)} or {_superseded_workflow_path(app)}" in summary["detail"]


@pytest.mark.parametrize(
    "kind, path",
    [
        ("gitlab", ".gitlab-ci.yml"),
        ("bitbucket", "bitbucket-pipelines.yml"),
        ("gitea", ".gitea/workflows/astrolift-ci.yml"),
    ],
)
def test_a_shared_host_file_is_named_and_never_touched(apps, kind, path):
    """These hosts keep the managed CI at one repository-wide path that can
    hold the operator's own jobs. No connection is even looked up."""
    app = apps[0]
    app.source_kind = kind
    app.ci_workflow_state = {"path": path}
    app.save(update_fields=["source_kind", "ci_workflow_state"])

    summary = remove_workflow_file_from_repo(app)

    assert summary["status"] == "unsupported_host"
    assert summary["retained"] == [path] and summary["removed"] == []
    assert summary["detail"].startswith(f"{path} on example/monorepo@main is shared")


def test_an_app_with_no_repo_has_nothing_to_remove(apps):
    app = apps[0]
    app.source_repo = ""
    app.save(update_fields=["source_repo"])

    assert remove_workflow_file_from_repo(app)["status"] == "no_repo"


def test_the_activity_finds_the_app_after_its_rows_are_soft_deleted(apps, host):
    """The deregister workflow runs the removal after the platform rows are
    soft-deleted."""
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    app.deleted_at = timezone.now()
    app.save(update_fields=["deleted_at"])
    assert not RegisteredApp.objects.filter(pk=app.pk).exists()

    summary = _remove_ci_workflow_sync(app.pk)

    assert summary["status"] == "removed" and summary["removed"] == [path]
    assert path not in host.files
