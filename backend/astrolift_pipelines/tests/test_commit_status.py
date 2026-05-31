"""Tests for astrolift_pipelines.commit_status (#104).

All HTTP calls are mocked via unittest.mock so no real network traffic is
generated.  The tests verify:

- GitHub state mapping (pending/success/failure/error) matches the table
  in the issue spec.
- GitLab state mapping.
- The correct URL is constructed (owner/repo/sha extracted from repo_url).
- Description is truncated to ≤ 140 chars.
- Non push/pr trigger_kinds are skipped by call_commit_status_after_run.
- Missing credential → silent skip, no HTTP call.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_pipelines.commit_status import (
    _GITHUB_STATE_MAP,
    _GITLAB_STATE_MAP,
    _parse_github_owner_repo,
    call_commit_status_after_run,
    post_github_commit_status,
    post_gitlab_commit_status,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pipeline(name: str = "ci", repo_url: str = "https://github.com/acme/myapp"):
    pipeline = SimpleNamespace(
        guid="pipeline-guid-1",
        name=name,
        repo_url=repo_url,
        registered_app=None,
        registered_app_id=None,
    )
    return pipeline


def _make_run(status: str = "success", trigger_kind: str = "push"):
    pipeline = _make_pipeline()
    run = SimpleNamespace(
        guid="run-guid-1",
        pipeline=pipeline,
        status=status,
        trigger_kind=trigger_kind,
    )
    return run


# ---------------------------------------------------------------------------
# State mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run_status, expected_github_state",
    [
        ("pending", "pending"),
        ("running", "pending"),
        ("success", "success"),
        ("failure", "failure"),
        ("timed_out", "failure"),
        ("cancelled", "error"),
    ],
)
def test_github_state_map(run_status, expected_github_state):
    assert _GITHUB_STATE_MAP.get(run_status) == expected_github_state


@pytest.mark.parametrize(
    "run_status, expected_gitlab_state",
    [
        ("pending", "pending"),
        ("running", "running"),
        ("success", "success"),
        ("failure", "failed"),
        ("timed_out", "failed"),
        ("cancelled", "canceled"),
    ],
)
def test_gitlab_state_map(run_status, expected_gitlab_state):
    assert _GITLAB_STATE_MAP.get(run_status) == expected_gitlab_state


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo_url, expected_owner, expected_repo",
    [
        ("https://github.com/acme/myapp", "acme", "myapp"),
        ("https://github.com/acme/myapp.git", "acme", "myapp"),
        ("https://github.com/acme/myapp/", "acme", "myapp"),
        ("git@github.com:acme/myapp.git", "acme", "myapp"),
        ("git@github.com:acme/myapp", "acme", "myapp"),
        ("https://gitlab.com/acme/myapp", "", ""),  # not a github url
        ("", "", ""),
    ],
)
def test_parse_github_owner_repo(repo_url, expected_owner, expected_repo):
    owner, repo = _parse_github_owner_repo(repo_url)
    assert owner == expected_owner
    assert repo == expected_repo


# ---------------------------------------------------------------------------
# GitHub posting
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, status=201):
        self._status = status

    def getcode(self):
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_post_github_commit_status_success():
    run = _make_run(status="success")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_github_commit_status(run, "ghp_token", commit_sha="abc123def456" * 3 + "abc1")

    mock_open.assert_called_once()
    request = mock_open.call_args[0][0]
    assert "acme/myapp/statuses/" in request.full_url
    assert b'"state": "success"' in request.data


def test_post_github_commit_status_pending():
    run = _make_run(status="running")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_github_commit_status(run, "ghp_token", commit_sha="a" * 40)

    request = mock_open.call_args[0][0]
    assert b'"state": "pending"' in request.data


def test_post_github_commit_status_failure():
    run = _make_run(status="failure")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_github_commit_status(run, "ghp_token", commit_sha="a" * 40)

    request = mock_open.call_args[0][0]
    assert b'"state": "failure"' in request.data


def test_post_github_commit_status_cancelled():
    run = _make_run(status="cancelled")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_github_commit_status(run, "ghp_token", commit_sha="a" * 40)

    request = mock_open.call_args[0][0]
    assert b'"state": "error"' in request.data


def test_post_github_commit_status_context_field():
    """context must be astrolift/{pipeline_name}."""
    run = _make_run(status="success")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_github_commit_status(run, "ghp_token", commit_sha="a" * 40)

    request = mock_open.call_args[0][0]
    import json
    body = json.loads(request.data)
    assert body["context"] == "astrolift/ci"


def test_post_github_commit_status_description_truncated():
    """description must be ≤ 140 characters."""
    long_desc = "x" * 200
    run = _make_run(status="success")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_github_commit_status(
            run, "ghp_token", commit_sha="a" * 40, description=long_desc
        )

    request = mock_open.call_args[0][0]
    import json
    body = json.loads(request.data)
    assert len(body["description"]) <= 140


def test_post_github_commit_status_bad_repo_url_skips(caplog):
    """Non-GitHub repo_url → silent skip, no HTTP call."""
    pipeline = _make_pipeline(repo_url="https://bitbucket.org/acme/myapp")
    run = SimpleNamespace(
        guid="run-guid-2",
        pipeline=pipeline,
        status="success",
        trigger_kind="push",
    )
    with patch("urllib.request.urlopen") as mock_open:
        post_github_commit_status(run, "ghp_token", commit_sha="a" * 40)

    mock_open.assert_not_called()


def test_post_github_commit_status_http_error_does_not_raise():
    import urllib.error
    run = _make_run(status="success")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(url="", code=422, msg="", hdrs=None, fp=None),
    ):
        # Must not raise — errors are swallowed and logged.
        post_github_commit_status(run, "ghp_token", commit_sha="a" * 40)


# ---------------------------------------------------------------------------
# GitLab posting
# ---------------------------------------------------------------------------


def test_post_gitlab_commit_status_success():
    run = _make_run(status="success")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_gitlab_commit_status(run, "glpat_token", commit_sha="b" * 40, project_id=42)

    mock_open.assert_called_once()
    request = mock_open.call_args[0][0]
    assert "/projects/42/statuses/" in request.full_url
    assert b'"state": "success"' in request.data


def test_post_gitlab_commit_status_running():
    run = _make_run(status="running")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_gitlab_commit_status(run, "glpat_token", commit_sha="b" * 40, project_id=42)

    request = mock_open.call_args[0][0]
    assert b'"state": "running"' in request.data


def test_post_gitlab_commit_status_cancelled():
    run = _make_run(status="cancelled")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_gitlab_commit_status(run, "glpat_token", commit_sha="b" * 40, project_id=42)

    request = mock_open.call_args[0][0]
    assert b'"state": "canceled"' in request.data


def test_post_gitlab_commit_status_namespace_project_encoded():
    """project_id with slashes must be URL-encoded in the path."""
    run = _make_run(status="success")
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(201)) as mock_open:
        post_gitlab_commit_status(
            run, "glpat_token", commit_sha="b" * 40, project_id="acme/myapp"
        )

    request = mock_open.call_args[0][0]
    assert "acme%2Fmyapp" in request.full_url


# ---------------------------------------------------------------------------
# call_commit_status_after_run dispatch
# ---------------------------------------------------------------------------


def test_call_commit_status_skips_non_push_trigger_kinds():
    """schedule / manual / api runs must not trigger a status post."""
    for trigger_kind in ("schedule", "manual", "api"):
        run = _make_run(status="success", trigger_kind=trigger_kind)
        with patch(
            "astrolift_pipelines.commit_status.post_github_commit_status"
        ) as mock_gh:
            call_commit_status_after_run(run, commit_sha="a" * 40)
        mock_gh.assert_not_called()


def test_call_commit_status_no_credential_silent_skip(caplog):
    """When no credential is configured the call logs and returns."""
    run = _make_run(status="success", trigger_kind="push")
    with (
        patch(
            "astrolift_pipelines.commit_status._get_github_token",
            return_value=None,
        ),
        patch(
            "astrolift_pipelines.commit_status._get_gitlab_credential",
            return_value=None,
        ),
        patch("urllib.request.urlopen") as mock_open,
    ):
        call_commit_status_after_run(run, commit_sha="a" * 40)

    mock_open.assert_not_called()


def test_call_commit_status_dispatches_github(monkeypatch):
    run = _make_run(status="success", trigger_kind="push")
    monkeypatch.setattr(
        "astrolift_pipelines.commit_status._get_github_token",
        lambda pipeline: "ghp_fake",
    )
    with patch(
        "astrolift_pipelines.commit_status.post_github_commit_status"
    ) as mock_gh:
        call_commit_status_after_run(run, commit_sha="a" * 40)

    mock_gh.assert_called_once()
    _, kwargs = mock_gh.call_args
    assert kwargs["commit_sha"] == "a" * 40


def test_call_commit_status_dispatches_gitlab_when_no_github(monkeypatch):
    run = _make_run(status="failure", trigger_kind="pull_request")
    monkeypatch.setattr(
        "astrolift_pipelines.commit_status._get_github_token",
        lambda pipeline: None,
    )
    monkeypatch.setattr(
        "astrolift_pipelines.commit_status._get_gitlab_credential",
        lambda pipeline: {"token": "glpat_fake", "project_id": 99},
    )
    with patch(
        "astrolift_pipelines.commit_status.post_gitlab_commit_status"
    ) as mock_gl:
        call_commit_status_after_run(run, commit_sha="b" * 40)

    mock_gl.assert_called_once()
    _, kwargs = mock_gl.call_args
    assert kwargs["commit_sha"] == "b" * 40
    assert kwargs["project_id"] == 99
