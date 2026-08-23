"""Pipeline runs report back to the commit that triggered them (#104).

`astrolift_pipelines/commit_status.py` had the whole outbound half written
and no caller: a pipeline run's result appeared in the Astrolift UI and
nowhere the developer who pushed the commit would look. Branch protection
could not gate on Astrolift at all, which is the feature the module's
`context` field exists for.

The credential half was worse than absent. It shipped two stubs: GitHub
read one platform-wide `settings.PIPELINE_GITHUB_STATUS_TOKEN` -- a single
credential across every tenant -- and GitLab returned None unconditionally,
so the GitLab branch could never execute. Wiring the module as written
would have looked like a working feature for exactly one configuration.

Both now resolve through `connection_resolver` with ORG_REPO_WRITE, which
is the module the codebase already built to answer "who does this act as"
after a near-identical picker reached for a human's personal OAuth token to
authenticate an autonomous platform write.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_pipelines import commit_status as cs
from astrolift_pipelines.commit_status import (
    _GITHUB_STATE_MAP,
    _GITLAB_STATE_MAP,
    _parse_github_owner_repo,
    call_commit_status_after_run,
    github_payload_for_run,
    post_commit_status_for_run,
    post_github_commit_status,
    post_gitlab_commit_status,
    repo_full_name_for,
    source_kind_for,
)
from astrolift_pipelines.tests import run_status_sites

_n = itertools.count(1)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pipeline(
    name: str = "ci",
    repo_url: str = "https://github.com/acme/myapp",
    registered_app=None,
):
    return SimpleNamespace(
        guid="pipeline-guid-1",
        name=name,
        repo_url=repo_url,
        organization_id=1,
        registered_app=registered_app,
        registered_app_id=None,
    )


def _make_run(status: str = "success", trigger_kind: str = "push", **kwargs):
    pipeline = kwargs.pop("pipeline", None) or _make_pipeline()
    return SimpleNamespace(
        guid="run-guid-1",
        pipeline=pipeline,
        status=status,
        trigger_kind=trigger_kind,
        commit_sha=kwargs.pop("commit_sha", "a" * 40),
        **kwargs,
    )


def _conn(kind: str = "github_app_install", **kwargs):
    return SimpleNamespace(
        kind=kind,
        is_orphaned=kwargs.pop("is_orphaned", False),
        installation_id=kwargs.pop("installation_id", "12345"),
        api_base_url=kwargs.pop("api_base_url", ""),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# State mapping (unchanged behaviour, kept pinned)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run_status, expected",
    [
        ("pending", "pending"),
        ("running", "pending"),
        ("success", "success"),
        ("failure", "failure"),
        ("timed_out", "failure"),
        ("cancelled", "error"),
    ],
)
def test_github_state_map(run_status, expected):
    assert _GITHUB_STATE_MAP.get(run_status) == expected


@pytest.mark.parametrize(
    "run_status, expected",
    [
        ("pending", "pending"),
        ("running", "running"),
        ("success", "success"),
        ("failure", "failed"),
        ("timed_out", "failed"),
        ("cancelled", "canceled"),
    ],
)
def test_gitlab_state_map(run_status, expected):
    assert _GITLAB_STATE_MAP.get(run_status) == expected


# ---------------------------------------------------------------------------
# Repo / host identification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo_url, owner, repo",
    [
        ("https://github.com/acme/myapp", "acme", "myapp"),
        ("https://github.com/acme/myapp.git", "acme", "myapp"),
        ("https://github.com/acme/myapp/", "acme", "myapp"),
        ("git@github.com:acme/myapp.git", "acme", "myapp"),
        ("git@github.com:acme/myapp", "acme", "myapp"),
        ("https://gitlab.com/acme/myapp", "", ""),
        ("", "", ""),
    ],
)
def test_parse_github_owner_repo(repo_url, owner, repo):
    assert _parse_github_owner_repo(repo_url) == (owner, repo)


@pytest.mark.parametrize(
    "repo_url, expected",
    [
        ("https://github.com/acme/myapp", "github"),
        ("git@github.com:acme/myapp.git", "github"),
        ("https://gitlab.com/acme/myapp", "gitlab"),
        ("https://gitlab.example.com/acme/myapp", "gitlab"),
        ("https://bitbucket.org/acme/myapp", "bitbucket"),
        ("https://svn.example.com/acme", ""),
        ("", ""),
    ],
)
def test_source_kind_sniffed_from_repo_url(repo_url, expected):
    """The app binding is nullable and pipelines are matched to webhooks by
    repo url, so plenty of real pipelines have no app to ask."""
    assert source_kind_for(_make_pipeline(repo_url=repo_url)) == expected


def test_a_bound_app_is_authoritative_over_the_url():
    """The app carries the host the org actually onboarded, which is the
    answer for a self-hosted instance whose URL says nothing useful."""
    app = SimpleNamespace(source_kind="gitea", source_repo="acme/myapp")
    pipeline = _make_pipeline(repo_url="https://git.internal/acme/myapp", registered_app=app)

    assert source_kind_for(pipeline) == "gitea"
    assert repo_full_name_for(pipeline) == "acme/myapp"


@pytest.mark.parametrize(
    "repo_url, expected",
    [
        ("https://gitlab.com/acme/myapp", "acme/myapp"),
        ("https://gitlab.com/acme/group/myapp.git", "acme/group/myapp"),
        ("git@gitlab.com:acme/myapp.git", "acme/myapp"),
    ],
)
def test_repo_full_name_from_url(repo_url, expected):
    assert repo_full_name_for(_make_pipeline(repo_url=repo_url)) == expected


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_the_payload_carries_the_context_branch_protection_targets():
    payload = github_payload_for_run(_make_run(status="success"))

    assert payload["context"] == "astrolift/ci"
    assert payload["state"] == "success"


def test_the_description_is_truncated_to_githubs_limit():
    payload = github_payload_for_run(_make_run(), description="x" * 300)

    assert len(payload["description"]) == 140


@pytest.mark.parametrize(
    "status, fragment",
    [
        ("success", "passed"),
        ("failure", "failed"),
        ("timed_out", "timed out"),
        ("running", "running"),
    ],
)
def test_the_default_description_says_what_happened(status, fragment):
    assert fragment in github_payload_for_run(_make_run(status=status))["description"]


# ---------------------------------------------------------------------------
# GitHub: delegates to the one poster in the codebase
# ---------------------------------------------------------------------------


def test_github_posts_through_the_shared_scm_poster():
    """Not a second transport. `astrolift_scm.commit_status.post_commit_status`
    already handles orphaned connections, App/OAuth/PAT credentials and
    GitHub Enterprise; only the payload differs for a pipeline run."""
    run = _make_run(status="success")

    with patch("astrolift_scm.commit_status.post_commit_status", return_value=True) as poster:
        assert post_github_commit_status(run, _conn(), commit_sha="a" * 40) is True

    kwargs = poster.call_args.kwargs
    assert (kwargs["owner"], kwargs["repo"]) == ("acme", "myapp")
    assert kwargs["sha"] == "a" * 40
    assert kwargs["payload"]["state"] == "success"


def test_github_skips_a_repo_url_it_cannot_parse(caplog):
    run = _make_run(pipeline=_make_pipeline(repo_url="not-a-url"))

    with patch("astrolift_scm.commit_status.post_commit_status") as poster:
        assert post_github_commit_status(run, _conn(), commit_sha="a" * 40) is False

    poster.assert_not_called()
    assert "skipped_bad_repo_url" in caplog.text


def test_a_raising_transport_does_not_propagate():
    """A commit status is advisory. It must never fail the run, and on the
    runner callback it must never turn a completion into a 500 that makes
    the runner retry a job that already finished."""
    run = _make_run()

    with patch("astrolift_scm.commit_status.post_commit_status", side_effect=RuntimeError("boom")):
        assert post_github_commit_status(run, _conn(), commit_sha="a" * 40) is False


# ---------------------------------------------------------------------------
# GitLab: the branch that could never run before
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status=201):
        self._status = status

    def getcode(self):
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _gitlab_pipeline():
    return _make_pipeline(repo_url="https://gitlab.com/acme/myapp")


def test_gitlab_posts_with_the_resolved_connections_token():
    run = _make_run(status="success", pipeline=_gitlab_pipeline())

    with (
        patch("astrolift_scm.providers.gitlab._token", return_value="glpat-x"),
        patch("astrolift_scm.providers.gitlab._api_base", return_value="https://gitlab.com"),
        patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen,
    ):
        assert post_gitlab_commit_status(run, _conn("gitlab_pat"), commit_sha="b" * 40) is True

    request = urlopen.call_args[0][0]
    assert request.full_url == ("https://gitlab.com/api/v4/projects/acme%2Fmyapp/statuses/" + "b" * 40)
    assert request.headers["Private-token"] == "glpat-x"


def test_gitlab_honours_a_self_hosted_api_base():
    run = _make_run(pipeline=_make_pipeline(repo_url="https://gitlab.acme.internal/team/app"))

    with (
        patch("astrolift_scm.providers.gitlab._token", return_value="glpat-x"),
        patch("astrolift_scm.providers.gitlab._api_base", return_value="https://gitlab.acme.internal"),
        patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen,
    ):
        post_gitlab_commit_status(run, _conn("gitlab_pat"), commit_sha="b" * 40)

    assert urlopen.call_args[0][0].full_url.startswith("https://gitlab.acme.internal/api/v4/")


def test_gitlab_encodes_the_namespaced_project_path():
    """GitLab addresses a project by URL-encoded `namespace/project`, and a
    nested group makes that two slashes deep."""
    run = _make_run(pipeline=_make_pipeline(repo_url="https://gitlab.com/acme/group/app"))

    with (
        patch("astrolift_scm.providers.gitlab._token", return_value="t"),
        patch("astrolift_scm.providers.gitlab._api_base", return_value="https://gitlab.com"),
        patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen,
    ):
        post_gitlab_commit_status(run, _conn("gitlab_pat"), commit_sha="c" * 40)

    assert "projects/acme%2Fgroup%2Fapp/statuses" in urlopen.call_args[0][0].full_url


def test_gitlab_uses_its_own_state_vocabulary():
    """GitLab has a real `canceled` state where GitHub only has `error`."""
    run = _make_run(status="cancelled", pipeline=_gitlab_pipeline())

    with (
        patch("astrolift_scm.providers.gitlab._token", return_value="t"),
        patch("astrolift_scm.providers.gitlab._api_base", return_value="https://gitlab.com"),
        patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen,
    ):
        post_gitlab_commit_status(run, _conn("gitlab_pat"), commit_sha="c" * 40)

    import json

    assert json.loads(urlopen.call_args[0][0].data)["state"] == "canceled"


def test_gitlab_an_unreadable_credential_is_a_skip_not_a_crash(caplog):
    run = _make_run(pipeline=_gitlab_pipeline())

    with (
        patch("astrolift_scm.providers.gitlab._token", side_effect=RuntimeError("decrypt failed")),
        patch("urllib.request.urlopen") as urlopen,
    ):
        assert post_gitlab_commit_status(run, _conn("gitlab_pat"), commit_sha="c" * 40) is False

    urlopen.assert_not_called()
    assert "skipped_bad_credential" in caplog.text


def test_gitlab_an_http_error_does_not_raise():
    run = _make_run(pipeline=_gitlab_pipeline())

    with (
        patch("astrolift_scm.providers.gitlab._token", return_value="t"),
        patch("astrolift_scm.providers.gitlab._api_base", return_value="https://gitlab.com"),
        patch("urllib.request.urlopen", side_effect=OSError("unreachable")),
    ):
        assert post_gitlab_commit_status(run, _conn("gitlab_pat"), commit_sha="c" * 40) is False


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trigger_kind", ["schedule", "manual", "api", "ci"])
def test_non_commit_triggers_are_skipped(trigger_kind):
    """A scheduled run has no commit to annotate."""
    run = _make_run(trigger_kind=trigger_kind)

    with patch.object(cs, "post_github_commit_status") as poster:
        assert call_commit_status_after_run(run) is False

    poster.assert_not_called()


def test_a_run_with_no_sha_is_skipped():
    run = _make_run(commit_sha="")

    with patch.object(cs, "post_github_commit_status") as poster:
        assert call_commit_status_after_run(run) is False

    poster.assert_not_called()


def test_an_unsupported_host_is_skipped_rather_than_half_posted(caplog):
    """Bitbucket and Gitea both have a build-status API and no driver here.
    Claiming support would log a post that never happened."""
    run = _make_run(pipeline=_make_pipeline(repo_url="https://bitbucket.org/acme/app"))

    with patch.object(cs, "_resolve_connection") as resolver:
        assert call_commit_status_after_run(run) is False

    resolver.assert_not_called()
    assert "skipped_unsupported_host" in caplog.text


def test_no_connection_is_a_silent_skip(caplog):
    run = _make_run()

    with (
        patch.object(cs, "_resolve_connection", return_value=None),
        patch.object(cs, "post_github_commit_status") as poster,
    ):
        assert call_commit_status_after_run(run) is False

    poster.assert_not_called()
    assert "skipped_no_credential" in caplog.text


def test_github_and_gitlab_dispatch_to_their_own_poster():
    gh = _make_run()
    gl = _make_run(pipeline=_gitlab_pipeline())

    with (
        patch.object(cs, "_resolve_connection", return_value=_conn()),
        patch.object(cs, "post_github_commit_status", return_value=True) as gh_poster,
        patch.object(cs, "post_gitlab_commit_status", return_value=True) as gl_poster,
    ):
        assert call_commit_status_after_run(gh) is True
        assert call_commit_status_after_run(gl) is True

    gh_poster.assert_called_once()
    gl_poster.assert_called_once()


def test_the_resolver_asks_for_org_repo_write_not_platform_write():
    """The distinction the resolver exists to enforce. PLATFORM_REPO_WRITE is
    App-only on GitHub, so asking for it would deny statuses to an App-less
    org that onboarded with an org OAuth or PAT connection -- which is the
    fallback ORG_REPO_WRITE exists to preserve."""
    pipeline = _make_pipeline()

    with patch("astrolift_scm.services.connection_resolver.resolve_connection") as resolve:
        cs._resolve_connection(pipeline, source_kind="github")

    assert resolve.call_args.kwargs["purpose"].value == "org_repo_write"


def test_a_resolution_error_is_a_skip_not_an_exception():
    from astrolift_scm.services.connection_resolver import ConnectionResolutionError

    with patch(
        "astrolift_scm.services.connection_resolver.resolve_connection",
        side_effect=ConnectionResolutionError("PRECONDITION", "no connection"),
    ):
        assert cs._resolve_connection(_make_pipeline(), source_kind="github") is None


def test_the_wrapper_swallows_anything_the_dispatcher_raises():
    """Every call site on a run's write path uses this. The status transition
    is already committed by then; an unreachable SCM host must not surface as
    a failed activity or a failed mutation."""
    with patch.object(cs, "call_commit_status_after_run", side_effect=RuntimeError("boom")):
        assert post_commit_status_for_run(_make_run()) is False


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------

_POSTER_NAMES = frozenset({"_post_commit_status", "post_commit_status_for_run"})


def test_every_run_status_transition_posts_a_commit_status():
    """The guard against this going dark again.

    A run's status is set in four places across two apps, and the failure
    mode is a fifth appearing without a post: the developer sees a stale
    check on their commit, or a branch-protection rule waits forever on a
    pending status that was never resolved.

    RUNNING counts, not just the terminal statuses. A rule requiring
    `astrolift/{pipeline}` needs the check to exist as pending before it can
    ever be satisfied.
    """
    sites = run_status_sites.find_sites(
        models=run_status_sites.COMMIT_STATUS_MODELS,
        statuses=run_status_sites.REPORTABLE_STATUSES,
    )

    # A structural test whose matcher matches nothing passes vacuously. The
    # metrics ratchet shipped in that state once; assert the sweep found the
    # sites before asserting anything about them.
    assert len(sites) >= 3, (
        f"the sweep found only {len(sites)} run-status transitions: {sorted(sites)}. "
        "Fix the matcher rather than the assertion."
    )

    offenders = [name for name, fn in sites.items() if not run_status_sites.calls_any(fn, _POSTER_NAMES)]
    assert not offenders, (
        "these change a pipeline run's status without reporting it to the SCM "
        f"host: {offenders}. The commit keeps a stale check, and a branch "
        "protection rule gating on Astrolift never resolves."
    )


def test_the_cancel_service_posts_a_commit_status():
    """The fourth transition, pinned by name rather than by shape.

    `cancel_pipeline_run` settles the run through the state machine, which
    assigns from a variable, so the sweep above cannot see it. Without this
    a cancelled run would leave a `pending` check on the commit forever --
    the worst of the four outcomes, because a branch-protection rule waits
    on it indefinitely.
    """
    target = run_status_sites.find_named("astrolift_pipelines/cancellation.py", "cancel_pipeline_run")

    assert target is not None, (
        "cancellation.cancel_pipeline_run is gone or renamed; this ratchet is "
        "now blind. Point it at whatever cancels a run instead."
    )
    assert run_status_sites.calls_any(target, _POSTER_NAMES), (
        "the cancel service settles a run without posting a commit status, so "
        "a cancelled run leaves a pending check that never resolves"
    )
