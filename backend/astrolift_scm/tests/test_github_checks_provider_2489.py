"""Pull request heads and check runs read through stored GitHub credentials (#2489)."""

import io
import json
import urllib.error
import urllib.parse
import uuid
from types import SimpleNamespace

import pytest

from astrolift_scm.providers import ProviderError, github_checks
from core.secrets import encrypt_at_rest

SHA = "ab" * 20


def _connection(kind="github_pat", base=""):
    secret = encrypt_at_rest(b"test-checks-token")
    return SimpleNamespace(
        kind=kind,
        api_base_url=base,
        account_login="acme",
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
    )


def _runs(count, *, start=0):
    return [
        {"id": n, "name": f"check-{n}", "status": "completed", "conclusion": "success"}
        for n in range(start, start + count)
    ]


class _GitHub:
    """The two read endpoints, recording each request."""

    def __init__(self, *, pull=None, pages=None, total=None):
        self.pull = {"number": 7, "head": {"sha": SHA.upper()}} if pull is None else pull
        self.pages = [_runs(2)] if pages is None else pages
        self.total = sum(len(page) for page in self.pages) if total is None else total
        self.requests = []

    def __call__(self, request, *, timeout):
        assert timeout == 10
        self.requests.append(request)
        url = urllib.parse.urlsplit(request.full_url)
        if url.path.endswith("/pulls/7"):
            return io.BytesIO(json.dumps(self.pull).encode())
        query = urllib.parse.parse_qs(url.query)
        assert url.path.endswith(f"/commits/{SHA}/check-runs")
        assert (query["filter"], query["per_page"]) == (["latest"], ["100"])
        page = int(query["page"][0])
        return io.BytesIO(
            json.dumps({"total_count": self.total, "check_runs": self.pages[page - 1]}).encode()
        )


@pytest.mark.parametrize(
    "kind,base,auth",
    [
        ("github_pat", "https://ghe.example/api/v3", "token test-checks-token"),
        ("github_oauth_user", "", "token test-checks-token"),
        ("github_app_install", "", "Bearer installation-test-token"),
    ],
)
def test_reads_the_head_then_its_check_runs_with_the_connection_credential(monkeypatch, kind, base, auth):
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token", lambda conn: "installation-test-token"
    )
    host = _GitHub()
    monkeypatch.setattr(github_checks.urllib.request, "urlopen", host)

    sha, runs = github_checks.read_pull_request_checks(
        _connection(kind, base), repo_full_name="acme/web.app", number=7
    )

    assert sha == SHA
    assert runs == _runs(2)
    expected = base or "https://api.github.com"
    assert [request.full_url for request in host.requests] == [
        f"{expected}/repos/acme/web.app/pulls/7",
        f"{expected}/repos/acme/web.app/commits/{SHA}/check-runs?filter=latest&per_page=100&page=1",
    ]
    for request in host.requests:
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") == auth
        assert request.get_header("X-github-api-version") == "2022-11-28"


def test_check_runs_are_read_page_by_page_until_the_listing_ends(monkeypatch):
    host = _GitHub(pages=[_runs(100), _runs(100, start=100), _runs(50, start=200)])
    monkeypatch.setattr(github_checks.urllib.request, "urlopen", host)

    _, runs = github_checks.read_pull_request_checks(_connection(), repo_full_name="acme/web", number=7)

    assert [run["id"] for run in runs] == list(range(250))
    assert len(host.requests) == 4


def test_a_full_last_page_ends_the_listing_at_its_total(monkeypatch):
    host = _GitHub(pages=[_runs(100), _runs(100, start=100)])
    monkeypatch.setattr(github_checks.urllib.request, "urlopen", host)

    _, runs = github_checks.read_pull_request_checks(_connection(), repo_full_name="acme/web", number=7)

    assert len(runs) == 200
    assert len(host.requests) == 3


def test_a_listing_longer_than_the_page_bound_is_refused_not_truncated(monkeypatch):
    pages = [_runs(100, start=100 * n) for n in range(github_checks.MAX_CHECK_RUN_PAGES)]
    host = _GitHub(pages=pages, total=1001)
    monkeypatch.setattr(github_checks.urllib.request, "urlopen", host)

    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(_connection(), repo_full_name="acme/web", number=7)

    assert caught.value.code == "TOO_MANY_CHECK_RUNS"
    assert len(host.requests) == 1 + github_checks.MAX_CHECK_RUN_PAGES


@pytest.mark.parametrize(
    "status,headers,code,recoverable",
    [
        (401, {}, "AUTH_FAILED", True),
        (403, {}, "AUTH_FAILED", True),
        (403, {"X-RateLimit-Remaining": "0"}, "RATE_LIMITED", True),
        (403, {"Retry-After": "30"}, "RATE_LIMITED", True),
        (429, {}, "RATE_LIMITED", True),
        (404, {}, "NOT_FOUND", False),
        (422, {}, "NOT_FOUND", False),
        (500, {}, "API_ERROR", False),
        (502, {}, "API_ERROR", False),
    ],
)
def test_http_errors_map_to_provider_codes_without_the_response_body(
    monkeypatch, status, headers, code, recoverable
):
    def request(req, *, timeout):
        raise urllib.error.HTTPError(
            req.full_url, status, "host error", headers, io.BytesIO(b"sensitive detail")
        )

    monkeypatch.setattr(github_checks.urllib.request, "urlopen", request)
    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(_connection(), repo_full_name="acme/web", number=7)
    assert (caught.value.code, caught.value.recoverable) == (code, recoverable)
    assert "sensitive" not in caught.value.message


@pytest.mark.parametrize(
    "error", [urllib.error.URLError("offline"), TimeoutError(), ConnectionResetError(), OSError("socket")]
)
def test_network_failures_are_recoverable(monkeypatch, error):
    def request(*args, **kwargs):
        raise error

    monkeypatch.setattr(github_checks.urllib.request, "urlopen", request)
    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(_connection(), repo_full_name="acme/web", number=7)
    assert (caught.value.code, caught.value.recoverable) == ("NETWORK", True)


@pytest.mark.parametrize(
    "pull",
    [{}, {"head": None}, {"head": {"sha": None}}, {"head": {"sha": "main"}}, {"head": {"sha": "abc123"}}, []],
)
def test_a_pull_request_without_a_full_head_sha_is_refused(monkeypatch, pull):
    host = _GitHub(pull=pull)
    monkeypatch.setattr(github_checks.urllib.request, "urlopen", host)
    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(_connection(), repo_full_name="acme/web", number=7)
    assert caught.value.code == "UNEXPECTED_SHAPE"
    assert len(host.requests) == 1


@pytest.mark.parametrize("payload", [b"not json", b"[]", b"{}", b'{"total_count": 1}', b'{"check_runs": []}'])
def test_an_invalid_check_run_listing_is_refused(monkeypatch, payload):
    def request(req, *, timeout):
        if req.full_url.endswith("/pulls/7"):
            return io.BytesIO(json.dumps({"head": {"sha": SHA}}).encode())
        return io.BytesIO(payload)

    monkeypatch.setattr(github_checks.urllib.request, "urlopen", request)
    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(_connection(), repo_full_name="acme/web", number=7)
    assert caught.value.code == "UNEXPECTED_SHAPE"


def test_non_github_and_unreadable_credentials_are_refused_before_any_request(monkeypatch):
    def request(*args, **kwargs):
        raise AssertionError("no request may be sent")

    monkeypatch.setattr(github_checks.urllib.request, "urlopen", request)
    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(_connection("gitlab_pat"), repo_full_name="acme/web", number=7)
    assert caught.value.code == "UNSUPPORTED"

    unreadable = _connection()
    unreadable.secret_ciphertext = b"not-valid-ciphertext"
    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(unreadable, repo_full_name="acme/web", number=7)
    assert caught.value.code == "CREDENTIAL_UNAVAILABLE"

    # An App installation whose stored private key is not a key at all.
    app = _connection("github_app_install")
    app.guid, app.app_client_id, app.installation_id = uuid.uuid4(), "Iv23liChecksFixture00", "99"
    with pytest.raises(ProviderError) as caught:
        github_checks.read_pull_request_checks(app, repo_full_name="acme/web", number=7)
    assert caught.value.code == "CREDENTIAL_UNAVAILABLE"
