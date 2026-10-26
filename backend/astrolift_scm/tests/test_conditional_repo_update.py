"""The host, not a preflight GET, fences inspected multi-file changes."""

import base64
from types import SimpleNamespace

import pytest

from astrolift_scm.providers import ProviderError, repo_writes


@pytest.mark.parametrize("base", ["https://api.github.test", "https://git.example.test/api/v3"])
def test_conditional_update_passes_exact_head_and_files_to_one_atomic_mutation(monkeypatch, base):
    calls = []
    monkeypatch.setattr(repo_writes, "_gh", lambda c: (base, {}))

    def host(method, url, headers, **kwargs):
        calls.append((method, url, kwargs["body"]))
        return 200, {"data": {"createCommitOnBranch": {"commit": {"oid": "c" * 40}}}}, None

    monkeypatch.setattr(repo_writes, "_http", host)
    sha = repo_writes.update_files(
        SimpleNamespace(kind="github_pat"),
        "acme/brain",
        "main",
        [repo_writes.TreeFile("client.config.json", b'{"brain":{}}')],
        "Change substrate",
        expected_head="a" * 40,
    )

    assert sha == "c" * 40
    [(method, url, body)] = calls
    assert method == "POST"
    assert url == (
        base.removesuffix("/api/v3") + "/api/graphql" if base.endswith("/api/v3") else base + "/graphql"
    )
    assert "createCommitOnBranch" in body["query"]
    assert body["variables"]["input"] == {
        "branch": {"repositoryNameWithOwner": "acme/brain", "refName": "main"},
        "expectedHeadOid": "a" * 40,
        "message": {"headline": "Change substrate"},
        "fileChanges": {
            "additions": [
                {"path": "client.config.json", "contents": base64.b64encode(b'{"brain":{}}').decode()}
            ]
        },
    }


def test_changed_branch_never_falls_back_to_a_fresh_parent_or_exposes_host_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(repo_writes, "_gh", lambda c: ("https://api.github.test", {}))

    def host(method, url, headers, **kwargs):
        calls.append((method, url))
        assert kwargs["body"]["variables"]["input"]["expectedHeadOid"] == "a" * 40
        return 200, {"errors": [{"message": "branch moved; PRIVATE_CONTENT_MARKER"}]}, None

    monkeypatch.setattr(repo_writes, "_http", host)
    with pytest.raises(ProviderError) as error:
        repo_writes.update_files(
            SimpleNamespace(kind="github_pat"),
            "acme/brain",
            "main",
            [repo_writes.TreeFile("client.config.json", b"{}")],
            "Change substrate",
            expected_head="a" * 40,
        )
    assert error.value.code == "PRECONDITION"
    assert "PRIVATE_CONTENT_MARKER" not in error.value.message
    assert calls == [("POST", "https://api.github.test/graphql")]


@pytest.mark.parametrize("kind", ["gitlab_pat", "gitea_pat", "bitbucket_oauth"])
def test_hosts_without_atomic_expected_head_support_refuse_before_io(monkeypatch, kind):
    def no_io(*args, **kwargs):
        pytest.fail("unsupported conditional writer performed I/O")

    monkeypatch.setattr(repo_writes, "_http", no_io)
    with pytest.raises(ProviderError) as error:
        repo_writes.update_files(
            SimpleNamespace(kind=kind),
            "acme/brain",
            "main",
            [repo_writes.TreeFile("client.config.json", b"{}")],
            "Change substrate",
            expected_head="a" * 40,
        )
    assert error.value.code == "UNSUPPORTED"
