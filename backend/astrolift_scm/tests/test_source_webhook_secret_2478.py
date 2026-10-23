"""Per-repo hooks on one connection share its webhook secret (#2478).

Every per-repo hook an OAuth or PAT connection installs is verified against
the one secret stored on the connection. ``install_astrolift_source_webhook``
used to mint a new secret on every call, the "already exists" refresh
included, and store it without telling GitHub or GitLab. Each earlier hook on
the connection then signed with a secret the receiver no longer had: every
delivery got a 401, and its pushes stopped deploying.

The hosts are faked at ``urllib.request.urlopen``. Everything above the wire
(connection picker, secret storage, the receiver view) runs against real
Postgres rows.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import itertools
import json
import re
import urllib.error
import urllib.parse

import pytest
from django.test import Client

from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.webhooks import install_astrolift_source_webhook
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

pytestmark = pytest.mark.django_db

PLATFORM = "https://astrolift.example.com"


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


@pytest.fixture(autouse=True)
def _platform(settings):
    settings.PLATFORM_API_URL = PLATFORM
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture
def team():
    org = Organization.objects.create(name="Acme", slug="acme-2478")
    return Team.objects.create(organization=org, name="Eng", slug="eng-2478")


def _app(team, slug: str, repo: str, *, kind: str = "github") -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=team.organization,
        team=team,
        name=slug,
        slug=slug,
        source_kind=kind,
        source_repo=repo,
        deploy_branch="main",
    )


def _connection(team, kind: str, *, webhook_secret: str | None = None, **extra) -> SourceConnection:
    token = encrypt_at_rest(b"token-never-leaves-the-fake")
    fields = {}
    if webhook_secret is not None:
        stored = encrypt_at_rest(webhook_secret.encode("utf-8"))
        fields = {
            "webhook_secret_backend_kind": stored.backend_kind,
            "webhook_secret_ciphertext": stored.backend_ref,
        }
    return SourceConnection.objects.create(
        organization=team.organization,
        kind=kind,
        display_name=kind,
        account_login="acme",
        secret_backend_kind=token.backend_kind,
        secret_ciphertext=token.backend_ref,
        is_active=True,
        **fields,
        **extra,
    )


def _stored_secret(connection: SourceConnection) -> str:
    connection.refresh_from_db()
    return decrypt(
        EncryptedSecret(
            backend_kind=connection.webhook_secret_backend_kind,
            backend_ref=bytes(connection.webhook_secret_ciphertext),
        )
    ).decode("utf-8")


def _receiver(app: RegisteredApp) -> str:
    return f"{PLATFORM}/api/webhooks/{app.source_kind}/{app.guid}/"


def _record_hook(app: RegisteredApp, hook_id: str) -> None:
    app.source_webhook_id = hook_id
    app.save(update_fields=["source_webhook_id", "updated_at", "version"])


class _Response:
    def __init__(self, payload, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _http_error(req, code: int, payload: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(req.full_url, code, "error", {}, io.BytesIO(json.dumps(payload).encode()))


class _FakeHost:
    """Repo hooks the way both hosts keep them for these calls: one hook per
    URL on a repository (a second create for it is refused), an update
    replaces the secret, and an id that is not on the repository is a 404.
    ``refuse`` makes the host answer one method with that status, and
    ``hidden`` leaves the listing empty, as for a token that cannot see the
    repository's hooks."""

    def __init__(self):
        self.hooks: dict[int, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.refuse: dict[str, int] = {}
        self.hidden = False
        self._ids = itertools.count(9001)

    def add_hook(self, repo: str, url: str, secret: str, **fields) -> str:
        hook_id = next(self._ids)
        self.hooks[hook_id] = {"repo": repo, "url": url, "secret": secret, **fields}
        return str(hook_id)

    def secret_for(self, repo: str, url: str) -> str:
        [hook] = [h for h in self.hooks.values() if h["repo"] == repo and h["url"] == url]
        return hook["secret"]

    def urlopen(self, req, timeout=None):
        split = urllib.parse.urlsplit(req.full_url)
        method = req.get_method()
        self.calls.append((method, split.path))
        if method in self.refuse:
            raise _http_error(req, self.refuse[method], {"message": "refused"})
        match = re.fullmatch(self.HOOKS_PATH, split.path)
        if match is None:
            raise _http_error(req, 404, {"message": "Not Found"})
        repo = urllib.parse.unquote(match.group(1))
        hook_id = match.group(2)
        body = json.loads(req.data.decode("utf-8")) if req.data else {}

        if hook_id is None and method == "POST":
            fields = self.kept(body)
            if any(h["repo"] == repo and h["url"] == fields["url"] for h in self.hooks.values()):
                raise _http_error(
                    req, 422, {"message": "Validation Failed: Hook already exists on this repository"}
                )
            return _Response({"id": int(self.add_hook(repo, **fields))}, status=201)
        if hook_id is None and method == "GET":
            query = urllib.parse.parse_qs(split.query)
            per_page, page = int(query["per_page"][0]), int(query["page"][0])
            listed = [
                self.listed(i, h) for i, h in self.hooks.items() if h["repo"] == repo and not self.hidden
            ]
            return _Response(listed[(page - 1) * per_page : page * per_page])
        hook = self.hooks.get(int(hook_id)) if hook_id else None
        if hook is not None and hook["repo"] == repo and method == self.UPDATE_METHOD:
            hook.update(self.kept(body))
            return _Response(self.listed(int(hook_id), hook))
        raise _http_error(req, 404, {"message": "Not Found"})


class FakeGithub(_FakeHost):
    HOOKS_PATH = r"/repos/([^/]+/[^/]+)/hooks(?:/(\d+))?"
    UPDATE_METHOD = "PATCH"

    def kept(self, body):
        # GitHub replaces the whole config on create and update alike: what
        # a PATCH leaves out is gone, and the content type falls back to
        # form encoding, which the receiver cannot parse.
        config = body["config"]
        return {
            "url": config["url"],
            "secret": config["secret"],
            "content_type": config.get("content_type", "form"),
        }

    def listed(self, hook_id, hook):
        return {
            "id": hook_id,
            "config": {"url": hook["url"], "content_type": hook.get("content_type", "json")},
        }


class FakeGitlab(_FakeHost):
    HOOKS_PATH = r"/api/v4/projects/([^/]+)/hooks(?:/(\d+))?"
    UPDATE_METHOD = "PUT"

    def kept(self, body):
        # GitLab requires the URL on every edit.
        return {"url": body["url"], "secret": body["token"]}

    def listed(self, hook_id, hook):
        return {"id": hook_id, "url": hook["url"], "push_events": True}


@pytest.fixture
def github(monkeypatch):
    host = FakeGithub()
    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", host.urlopen)
    return host


@pytest.fixture
def gitlab(monkeypatch):
    host = FakeGitlab()
    monkeypatch.setattr("astrolift_scm.providers.gitlab.urllib.request.urlopen", host.urlopen)
    return host


def _push(app: RegisteredApp, *, signed_with: str):
    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "after": "a" * 40,
            "repository": {"full_name": app.source_repo},
        }
    ).encode("utf-8")
    signature = "sha256=" + hmac.new(signed_with.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return Client().post(
        f"/api/webhooks/github/{app.guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_GITHUB_DELIVERY="24780000-0000-0000-0000-000000000001",
    )


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def test_a_second_app_on_the_connection_leaves_the_first_apps_hook_verifying(team, github):
    """The reported bug: installing B's hook replaced the secret A's hook
    signs with, so A's pushes were refused with a 401."""
    _connection(team, "github_oauth_user")
    web = _app(team, "web", "acme/web")
    api = _app(team, "api", "acme/api")

    assert install_astrolift_source_webhook(web).status == "created"
    assert install_astrolift_source_webhook(api).status == "created"

    response = _push(web, signed_with=github.secret_for("acme/web", _receiver(web)))

    assert response.status_code == 200, response.content
    assert response.json()["detail"] == "push handled"


def test_the_first_install_on_a_connection_mints_the_one_secret_every_hook_carries(team, github):
    connection = _connection(team, "github_pat")
    web = _app(team, "web", "acme/web")
    api = _app(team, "api", "acme/api")

    install_astrolift_source_webhook(web)
    install_astrolift_source_webhook(api)

    held = {github.secret_for("acme/web", _receiver(web)), github.secret_for("acme/api", _receiver(api))}
    assert held == {_stored_secret(connection)}


def test_a_stored_secret_is_reused_by_a_new_hook(team, github):
    connection = _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")

    result = install_astrolift_source_webhook(web)

    assert result.status == "created"
    assert github.secret_for("acme/web", _receiver(web)) == "whsec-current"
    assert _stored_secret(connection) == "whsec-current"


def test_refresh_keeps_the_stored_secret_and_patches_the_hook(team, github):
    connection = _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")
    hook_id = github.add_hook("acme/web", _receiver(web), "whsec-current")
    _record_hook(web, hook_id)

    result = install_astrolift_source_webhook(web)

    assert result.status == "refreshed"
    assert result.hook_id == hook_id
    # The recorded id is used as is: no listing.
    assert github.calls == [("POST", "/repos/acme/web/hooks"), ("PATCH", f"/repos/acme/web/hooks/{hook_id}")]
    assert github.hooks[int(hook_id)]["content_type"] == "json"
    assert github.secret_for("acme/web", _receiver(web)) == "whsec-current"
    assert _stored_secret(connection) == "whsec-current"
    web.refresh_from_db()
    assert web.source_webhook_installed_at is not None


def test_a_hook_left_on_an_old_secret_verifies_after_a_refresh(team, github):
    """A hook an earlier install broke is repaired by refreshing its app."""
    _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")
    _record_hook(web, github.add_hook("acme/web", _receiver(web), "whsec-replaced-by-a-later-install"))
    assert _push(web, signed_with=github.secret_for("acme/web", _receiver(web))).status_code == 401

    assert install_astrolift_source_webhook(web).status == "refreshed"

    response = _push(web, signed_with=github.secret_for("acme/web", _receiver(web)))
    assert response.status_code == 200, response.content


def test_a_refresh_with_no_recorded_hook_finds_it_by_url_and_records_it(team, github):
    _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")
    unrelated = github.add_hook("acme/web", "https://ci.example.com/hook", "their-secret")
    hook_id = github.add_hook("acme/web", _receiver(web), "whsec-old")

    result = install_astrolift_source_webhook(web)

    assert result.status == "refreshed"
    assert result.hook_id == hook_id
    web.refresh_from_db()
    assert web.source_webhook_id == hook_id
    assert github.secret_for("acme/web", _receiver(web)) == "whsec-current"
    assert github.hooks[int(unrelated)] == {
        "repo": "acme/web",
        "url": "https://ci.example.com/hook",
        "secret": "their-secret",
    }


def test_a_recorded_hook_the_repo_does_not_have_is_found_again_by_url(team, github):
    """After a source change the recorded id can name a hook on the old
    repository; GitHub answers 404 for it on the new one."""
    _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")
    old_repo_hook = github.add_hook("acme/old-name", _receiver(web), "whsec-old")
    _record_hook(web, old_repo_hook)
    hook_id = github.add_hook("acme/web", _receiver(web), "whsec-old")

    result = install_astrolift_source_webhook(web)

    assert result.status == "refreshed"
    assert result.hook_id == hook_id
    web.refresh_from_db()
    assert web.source_webhook_id == hook_id
    assert github.secret_for("acme/web", _receiver(web)) == "whsec-current"
    assert github.hooks[int(old_repo_hook)]["secret"] == "whsec-old"


def test_the_hook_is_found_past_the_first_page_of_the_listing(team, github):
    _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")
    for n in range(100):
        github.add_hook("acme/web", f"https://ci.example.com/hook/{n}", "their-secret")
    hook_id = github.add_hook("acme/web", _receiver(web), "whsec-old")

    result = install_astrolift_source_webhook(web)

    assert result.status == "refreshed"
    assert result.hook_id == hook_id
    assert github.secret_for("acme/web", _receiver(web)) == "whsec-current"


def test_a_refused_update_is_not_reported_as_refreshed(team, github):
    connection = _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")
    hook_id = github.add_hook("acme/web", _receiver(web), "whsec-old")
    _record_hook(web, hook_id)
    github.refuse["PATCH"] = 403

    result = install_astrolift_source_webhook(web)

    assert result.status == "fetch_failed"
    assert "403" in result.error
    assert _stored_secret(connection) == "whsec-current"
    web.refresh_from_db()
    assert web.source_webhook_installed_at is None


def test_a_hook_the_connection_cannot_list_is_not_reported_as_refreshed(team, github):
    _connection(team, "github_oauth_user", webhook_secret="whsec-current")
    web = _app(team, "web", "acme/web")
    github.add_hook("acme/web", _receiver(web), "whsec-old")
    github.hidden = True

    result = install_astrolift_source_webhook(web)

    assert result.status == "fetch_failed"
    assert _receiver(web) in result.error
    assert not any(method == "PATCH" for method, _ in github.calls)
    web.refresh_from_db()
    assert web.source_webhook_id == ""
    assert web.source_webhook_installed_at is None


def test_a_minted_secret_is_stored_only_after_the_host_accepts_the_hook(team, github):
    connection = _connection(team, "github_oauth_user")
    web = _app(team, "web", "acme/web")
    github.refuse["POST"] = 403

    result = install_astrolift_source_webhook(web)

    assert result.status == "fetch_failed"
    connection.refresh_from_db()
    assert connection.webhook_secret_backend_kind == ""
    assert bytes(connection.webhook_secret_ciphertext) == b""


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(lambda: ("local_fernet", b"not-a-fernet-token"), id="not-a-token"),
        pytest.param(lambda: ("a_backend_this_install_lacks", b"arn:elsewhere"), id="unknown-backend"),
        pytest.param(lambda: ("local_fernet", encrypt_at_rest(b"\xff\xfe").backend_ref), id="not-utf-8"),
        pytest.param(lambda: ("local_fernet", encrypt_at_rest(b"").backend_ref), id="empty"),
    ],
)
def test_a_stored_secret_that_verifies_nothing_is_replaced_by_the_one_the_new_hook_carries(
    team, github, stored
):
    backend_kind, ciphertext = stored()
    connection = _connection(
        team,
        "github_oauth_user",
        webhook_secret_backend_kind=backend_kind,
        webhook_secret_ciphertext=ciphertext,
    )
    web = _app(team, "web", "acme/web")

    assert install_astrolift_source_webhook(web).status == "created"

    held = github.secret_for("acme/web", _receiver(web))
    assert held
    assert _stored_secret(connection) == held


@pytest.mark.parametrize("hook_exists", [False, True], ids=["created", "refreshed"])
def test_an_install_never_writes_back_a_secret_it_did_not_mint(team, github, monkeypatch, hook_exists):
    """A rotation that lands while the host call is in flight stands."""
    connection = _connection(team, "github_oauth_user", webhook_secret="whsec-before")
    web = _app(team, "web", "acme/web")
    if hook_exists:
        _record_hook(web, github.add_hook("acme/web", _receiver(web), "whsec-before"))
    answer = github.urlopen

    def rotate_then_answer(req, timeout=None):
        rotated = encrypt_at_rest(b"whsec-rotated")
        SourceConnection.objects.filter(pk=connection.pk).update(
            webhook_secret_backend_kind=rotated.backend_kind,
            webhook_secret_ciphertext=rotated.backend_ref,
        )
        return answer(req, timeout)

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", rotate_then_answer)

    assert install_astrolift_source_webhook(web).status == ("refreshed" if hook_exists else "created")

    assert _stored_secret(connection) == "whsec-rotated"


def test_an_undecryptable_stored_secret_is_replaced_by_the_one_a_refresh_applies(team, github):
    connection = _connection(
        team,
        "github_oauth_user",
        webhook_secret_backend_kind="local_fernet",
        webhook_secret_ciphertext=b"not-a-fernet-token",
    )
    web = _app(team, "web", "acme/web")
    _record_hook(web, github.add_hook("acme/web", _receiver(web), "whsec-lost-with-the-old-key"))

    assert install_astrolift_source_webhook(web).status == "refreshed"

    held = github.secret_for("acme/web", _receiver(web))
    assert held != "whsec-lost-with-the-old-key"
    assert _stored_secret(connection) == held
    assert _push(web, signed_with=held).status_code == 200


def test_a_minted_secret_is_not_stored_when_the_host_refuses_the_update(team, github):
    connection = _connection(
        team,
        "github_oauth_user",
        webhook_secret_backend_kind="local_fernet",
        webhook_secret_ciphertext=b"not-a-fernet-token",
    )
    web = _app(team, "web", "acme/web")
    _record_hook(web, github.add_hook("acme/web", _receiver(web), "whsec-lost-with-the-old-key"))
    github.refuse["PATCH"] = 403

    assert install_astrolift_source_webhook(web).status == "fetch_failed"

    connection.refresh_from_db()
    assert bytes(connection.webhook_secret_ciphertext) == b"not-a-fernet-token"


def test_an_app_installation_never_stores_a_webhook_secret_here(team, monkeypatch):
    """The App's deliveries are signed with the App's own secret."""
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: "ghs_installation_token",
    )
    calls = []

    def urlopen(req, timeout=None):
        calls.append((req.get_method(), urllib.parse.urlsplit(req.full_url).path))
        return _Response({"total_count": 1, "repositories": [{"full_name": "acme/web"}]})

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", urlopen)
    connection = _connection(team, "github_app_install", webhook_secret="app-whsec", installation_id="987")
    web = _app(team, "web", "acme/web")

    assert install_astrolift_source_webhook(web).status == "app_delivers"

    assert _stored_secret(connection) == "app-whsec"
    assert calls == [("GET", "/installation/repositories")]


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def test_gitlab_hooks_of_two_apps_carry_the_connections_one_secret(team, gitlab):
    connection = _connection(team, "gitlab_oauth_user")
    web = _app(team, "web", "acme/web", kind="gitlab")
    api = _app(team, "api", "acme/api", kind="gitlab")

    assert install_astrolift_source_webhook(web).status == "created"
    assert install_astrolift_source_webhook(api).status == "created"

    held = {gitlab.secret_for("acme/web", _receiver(web)), gitlab.secret_for("acme/api", _receiver(api))}
    assert held == {_stored_secret(connection)}


def test_gitlab_refresh_keeps_the_stored_secret_and_updates_the_hook(team, gitlab):
    connection = _connection(team, "gitlab_pat", webhook_secret="gl-current")
    web = _app(team, "web", "acme/web", kind="gitlab")
    hook_id = gitlab.add_hook("acme/web", _receiver(web), "gl-replaced-by-a-later-install")
    _record_hook(web, hook_id)

    result = install_astrolift_source_webhook(web)

    assert result.status == "refreshed"
    assert result.hook_id == hook_id
    assert ("PUT", f"/api/v4/projects/acme%2Fweb/hooks/{hook_id}") in gitlab.calls
    assert gitlab.secret_for("acme/web", _receiver(web)) == "gl-current"
    assert _stored_secret(connection) == "gl-current"


def test_gitlab_refresh_with_no_recorded_hook_finds_it_by_url_and_records_it(team, gitlab):
    _connection(team, "gitlab_oauth_user", webhook_secret="gl-current")
    web = _app(team, "web", "acme/web", kind="gitlab")
    gitlab.add_hook("acme/web", "https://ci.example.com/hook", "their-token")
    hook_id = gitlab.add_hook("acme/web", _receiver(web), "gl-old")

    result = install_astrolift_source_webhook(web)

    assert result.status == "refreshed"
    assert result.hook_id == hook_id
    web.refresh_from_db()
    assert web.source_webhook_id == hook_id
    assert gitlab.secret_for("acme/web", _receiver(web)) == "gl-current"
    assert gitlab.secret_for("acme/web", "https://ci.example.com/hook") == "their-token"


def test_gitlab_recorded_hook_the_project_does_not_have_is_found_again_by_url(team, gitlab):
    _connection(team, "gitlab_oauth_user", webhook_secret="gl-current")
    web = _app(team, "web", "acme/web", kind="gitlab")
    old_project_hook = gitlab.add_hook("acme/old-name", _receiver(web), "gl-old")
    _record_hook(web, old_project_hook)
    hook_id = gitlab.add_hook("acme/web", _receiver(web), "gl-old")

    result = install_astrolift_source_webhook(web)

    assert result.status == "refreshed"
    assert result.hook_id == hook_id
    web.refresh_from_db()
    assert web.source_webhook_id == hook_id
    assert gitlab.secret_for("acme/web", _receiver(web)) == "gl-current"
    assert gitlab.hooks[int(old_project_hook)]["secret"] == "gl-old"


def test_gitlab_refused_update_is_not_reported_as_refreshed(team, gitlab):
    connection = _connection(team, "gitlab_pat", webhook_secret="gl-current")
    web = _app(team, "web", "acme/web", kind="gitlab")
    _record_hook(web, gitlab.add_hook("acme/web", _receiver(web), "gl-old"))
    gitlab.refuse["PUT"] = 403

    result = install_astrolift_source_webhook(web)

    assert result.status == "fetch_failed"
    assert _stored_secret(connection) == "gl-current"
    assert gitlab.secret_for("acme/web", _receiver(web)) == "gl-old"


def test_gitlab_undecryptable_stored_secret_is_replaced_by_the_one_a_refresh_applies(team, gitlab):
    connection = _connection(
        team,
        "gitlab_oauth_user",
        webhook_secret_backend_kind="local_fernet",
        webhook_secret_ciphertext=b"not-a-fernet-token",
    )
    web = _app(team, "web", "acme/web", kind="gitlab")
    _record_hook(web, gitlab.add_hook("acme/web", _receiver(web), "gl-lost-with-the-old-key"))

    assert install_astrolift_source_webhook(web).status == "refreshed"

    held = gitlab.secret_for("acme/web", _receiver(web))
    assert held != "gl-lost-with-the-old-key"
    assert _stored_secret(connection) == held


# ---------------------------------------------------------------------------
# Listing bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "module", "find", "row"),
    [
        pytest.param(
            "github_pat",
            "github",
            "find_github_webhook",
            lambda n: {"id": n, "config": {"url": f"https://ci.example.com/{n}"}},
            id="github",
        ),
        pytest.param(
            "gitlab_pat",
            "gitlab",
            "find_gitlab_webhook",
            lambda n: {"id": n, "url": f"https://ci.example.com/{n}"},
            id="gitlab",
        ),
    ],
)
def test_the_hook_listing_stops_at_ten_pages_when_the_host_never_ends_it(
    team, monkeypatch, kind, module, find, row
):
    """A host that ignores ``page`` would otherwise keep the request looping."""
    import importlib

    provider = importlib.import_module(f"astrolift_scm.providers.{module}")
    pages = []

    def urlopen(req, timeout=None):
        pages.append(urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)["page"][0])
        return _Response([row(n) for n in range(100)])

    monkeypatch.setattr(f"astrolift_scm.providers.{module}.urllib.request.urlopen", urlopen)
    connection = _connection(team, kind)

    found = getattr(provider, find)(connection, repo_full_name="acme/web", target_url=f"{PLATFORM}/hook/")

    assert found is None
    assert pages == [str(n) for n in range(1, 11)]
