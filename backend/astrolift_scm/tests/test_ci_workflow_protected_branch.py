"""A protected deploy branch routes the CI workflow sync through a PR.

SteadyMD's smd-white-label protects ``main``. The protection probe read
``/branches/main/protection``, which answers 404 to any non-admin token, so
the sync PUT straight onto ``main``; GitHub's refusal then surfaced as
"refused the conditional file write; review the current file", which named
neither the branch nor the cause.

Pure unit: GitHub's HTTP layer is stubbed; no DB and no network.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from astrolift_scm.tests.test_auth_error_messages import _PatConn


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _branch_response(protected: bool):
    def _open(req, timeout=10):
        assert req.full_url.endswith("/branches/main"), req.full_url
        return _Resp(json.dumps({"name": "main", "protected": protected}).encode())

    return _open


@pytest.mark.parametrize("protected", [True, False])
def test_protection_reads_the_branch_flag_any_reader_sees(monkeypatch, protected):
    from astrolift_scm.services.workflow_sync import _is_github_branch_protected

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.urllib.request.urlopen", _branch_response(protected)
    )
    assert _is_github_branch_protected(_PatConn(), repo_full_name="acme/api", branch="main") is protected


def _put_refused(code: int, body: bytes):
    calls = {"n": 0}

    def _open(req, timeout=10):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(body))

    return _open


def _put(monkeypatch, code, body):
    from astrolift_scm.providers import ProviderError, put_file

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", _put_refused(code, body))
    with pytest.raises(ProviderError) as exc:
        put_file(
            _PatConn(),
            repo_full_name="acme/api",
            path=".github/workflows/astrolift-ci.yml",
            branch="main",
            content="name: ci\n",
            commit_message="chore: ci",
            expected_absent=True,
        )
    return exc.value


def test_a_protected_branch_refusal_is_not_reported_as_a_file_conflict(monkeypatch):
    err = _put(
        monkeypatch,
        409,
        b'{"message":"Repository rule violations found\\n\\nChanges must be made through a pull request."}',
    )
    assert err.code != "CONFLICT"
    assert "pull request" in err.message


def test_a_missing_workflow_scope_is_named(monkeypatch):
    err = _put(
        monkeypatch,
        422,
        b'{"message":"refusing to allow an OAuth App to create or update workflow without `workflow` scope"}',
    )
    assert err.code == "MISSING_WORKFLOW_SCOPE"


def test_a_sha_precondition_failure_is_still_a_conflict(monkeypatch):
    err = _put(monkeypatch, 422, b'{"message":"Invalid request.\\n\\n\\"sha\\" wasn\'t supplied."}')
    assert err.code == "CONFLICT"
