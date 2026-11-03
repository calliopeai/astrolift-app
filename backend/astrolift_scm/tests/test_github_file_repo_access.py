"""``repo_visible`` tells a manifest read apart from a repo the credential can't see.

A 404 on a file is still "file missing" for the fetch (write paths depend on
that). Only the manifest pull asks ``repo_visible`` to explain the miss."""

from __future__ import annotations

import io
import urllib.error

import pytest

from astrolift_scm.providers import github


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))


@pytest.fixture
def stub_connection(monkeypatch):
    monkeypatch.setattr(github, "_token", lambda connection: "tok")
    monkeypatch.setattr(github, "_api_base", lambda connection: "https://api.example")


def _repo_status(monkeypatch, code: int | None):
    """code None -> the repo read succeeds; otherwise it fails with that status."""

    def _open(req, timeout=None):
        if code is None:
            return _Resp(b"{}")
        raise _http(req.full_url, code)

    monkeypatch.setattr(github.urllib.request, "urlopen", _open)


def test_repo_visible_true_when_repo_reads(monkeypatch, stub_connection):
    _repo_status(monkeypatch, None)
    assert github.repo_visible(object(), "ConflictHQ/leo-brain") is True


def test_repo_visible_false_on_404(monkeypatch, stub_connection):
    _repo_status(monkeypatch, 404)
    assert github.repo_visible(object(), "ConflictHQ/leo-brain") is False


def test_repo_visible_true_on_other_failures(monkeypatch, stub_connection):
    """A flaky probe must not blame the operator's access."""
    _repo_status(monkeypatch, 500)
    assert github.repo_visible(object(), "ConflictHQ/leo-brain") is True


def test_file_404_still_reads_as_missing(monkeypatch, stub_connection):
    def _open(req, timeout=None):
        raise _http(req.full_url, 404)

    monkeypatch.setattr(github.urllib.request, "urlopen", _open)
    assert (
        github.fetch_github_file(object(), repo_full_name="ConflictHQ/leo-brain", path="astrolift.toml", ref="main")
        is None
    )
