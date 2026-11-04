"""Following a repository renamed on GitHub (#2478).

The GitHub App delivers ``repository`` events to its one webhook URL. A
``renamed`` delivery points the organization's apps on the old name at the
new one with the change ``setAppSource`` makes, so pushes under the new name
keep finding them. Deliveries go through the real receiver, signed with the
App's webhook secret. GitHub's installation listing is a fake ``urlopen`` and
autowire is recorded where it would run; everything else is real rows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from django.test import Client

from astrolift_operations.models import AuditEvent
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection, WebhookDelivery
from core.secrets import encrypt_at_rest
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db

OLD = "ConflictHQ/fossilhub"
NEW = "ConflictHQ/fossilrepos"
SECRET = "whsec_app_2478"

OUTSIDE = (
    f"{NEW} is not in the GitHub App installation on ConflictHQ. "
    "Give the App access to the repository on GitHub, then retry."
)
TAKEN = "another app in this organization is registered from this repository and manifest path"


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    """The debug toolbar middleware answers 500 under the test client."""
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def github(monkeypatch):
    """The repositories of the App installation. Every request is recorded."""
    state = SimpleNamespace(installed=[NEW], calls=[])
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token", lambda connection: "ghs_fake"
    )

    def urlopen(request, timeout=None):
        url = request.full_url
        state.calls.append(url)
        if "/installation/repositories" in url:
            names = state.installed
            return _Response({"total_count": len(names), "repositories": [{"full_name": n} for n in names]})
        raise AssertionError(f"unexpected GitHub call {url}")

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", urlopen)
    return state


@pytest.fixture
def autowired(monkeypatch):
    """Each autowire run, as (app slug, the repository it ran against, actor)."""
    runs: list[tuple] = []
    monkeypatch.setattr(
        "astrolift_scm.services.source_change.run_autowire",
        lambda app, *, actor=None: runs.append((app.slug, app.source_repo, actor)),
    )
    return runs


def _app_connection(org, *, installation_id: str) -> SourceConnection:
    pem = encrypt_at_rest(b"fake-pem")
    secret = encrypt_at_rest(SECRET.encode())
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        account_login="ConflictHQ",
        installation_id=installation_id,
        secret_backend_kind=pem.backend_kind,
        secret_ciphertext=pem.backend_ref,
        webhook_secret_backend_kind=secret.backend_kind,
        webhook_secret_ciphertext=secret.backend_ref,
        is_active=True,
    )


@pytest.fixture
def world():
    world = ScopeWorld("rename2478")
    app = world.medops_app
    app.source_kind = "github"
    app.source_repo = OLD
    app.source_url = f"https://github.com/{OLD}"
    app.save()
    world.platform_app.source_repo = "ConflictHQ/gateway"
    world.platform_app.save()
    world.conn = _app_connection(world.org, installation_id="987")
    return world


def _renamed(*, installation_id=987, action="renamed", changes=True) -> bytes:
    payload = {
        "action": action,
        "repository": {"name": "fossilrepos", "full_name": NEW, "owner": {"login": "ConflictHQ"}},
        "installation": {"id": installation_id},
    }
    if changes:
        payload["changes"] = {"repository": {"name": {"from": "fossilhub"}}}
    return json.dumps(payload).encode()


def _push(repo: str) -> bytes:
    return json.dumps(
        {
            "ref": "refs/heads/main",
            "after": "abc123",
            "repository": {"full_name": repo},
            "installation": {"id": 987},
        }
    ).encode()


def _deliver(conn, body: bytes, *, event="repository", delivery="d-1", secret=SECRET):
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return Client().post(
        f"/app/auth1/scm/github/webhook/{conn.guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
        HTTP_X_GITHUB_EVENT=event,
        HTTP_X_GITHUB_DELIVERY=delivery,
    )


def _snapshot(app):
    app.refresh_from_db()
    return (app.source_repo, app.source_url, app.autowire_state, app.version)


def test_a_renamed_repository_moves_its_app_and_the_next_push_finds_it(world, github, autowired):
    app = world.medops_app
    before = _deliver(world.conn, _push(NEW), event="push", delivery="p-0")
    assert before.json()["ignored"] == "no_matching_app"

    resp = _deliver(world.conn, _renamed(), delivery="d-1")

    assert resp.status_code == 202, resp.content
    assert resp.json() == {
        "ok": True,
        "renamed": {"from": OLD, "to": NEW},
        "moved": [{"app": app.slug, "source_repo": NEW}],
    }
    app.refresh_from_db()
    assert (app.source_repo, app.source_url) == (NEW, f"https://github.com/{NEW}")
    assert autowired == [(app.slug, NEW, None)]
    row = AuditEvent.objects.get(action="app.set_source")
    assert (row.actor_kind, row.decision, row.organization_id, row.target_kind, row.target_id) == (
        "system",
        "ALLOW",
        world.org.pk,
        "RegisteredApp",
        str(app.guid),
    )
    assert {key: row.data[key] for key in ("error_code", "previous_source_repo", "source_repo")} == {
        "error_code": None,
        "previous_source_repo": OLD,
        "source_repo": NEW,
    }
    assert (row.data["trigger"], row.data["delivery_id"], row.data["renamed_to"]) == (
        "github.repository.renamed",
        "d-1",
        NEW,
    )
    after = _deliver(world.conn, _push(NEW), event="push", delivery="p-1").json()
    assert "ignored" not in after
    assert after["repo"] == NEW


def test_every_app_on_the_old_name_moves_whatever_case_it_was_stored_in(world, github, autowired):
    # Typed by hand at registration; GitHub's spelling is stored from now on.
    world.medops_app.source_repo = OLD.lower()
    world.medops_app.save()
    # A second app of the monorepo.
    world.platform_app.source_repo = OLD
    world.platform_app.manifest_path = "apps/gateway/astrolift.toml"
    world.platform_app.save()

    resp = _deliver(world.conn, _renamed())

    assert [moved["app"] for moved in resp.json()["moved"]] == [
        world.medops_app.slug,
        world.platform_app.slug,
    ]
    assert sorted(
        RegisteredApp.objects.filter(organization=world.org).values_list("source_repo", flat=True)
    ) == [
        NEW,
        NEW,
    ]
    assert autowired == [(world.medops_app.slug, NEW, None), (world.platform_app.slug, NEW, None)]
    rows = AuditEvent.objects.filter(action="app.set_source").order_by("pk")
    assert [(row.target_id, row.data["previous_source_repo"]) for row in rows] == [
        (str(world.medops_app.guid), OLD.lower()),
        (str(world.platform_app.guid), OLD),
    ]


@pytest.mark.parametrize("installation", [987, 555], ids=["own-installation", "other-installation"])
def test_only_the_delivering_installations_organization_moves(world, github, autowired, installation):
    other = ScopeWorld("rename2478other")
    other.medops_app.source_repo = OLD
    other.medops_app.save()
    _app_connection(other.org, installation_id="555")
    moves, stays = (world, other) if installation == 987 else (other, world)

    # One App delivers every installation's events to the same URL.
    resp = _deliver(world.conn, _renamed(installation_id=installation))

    assert [moved["app"] for moved in resp.json()["moved"]] == [moves.medops_app.slug]
    moves.medops_app.refresh_from_db()
    stays.medops_app.refresh_from_db()
    assert (moves.medops_app.source_repo, stays.medops_app.source_repo) == (NEW, OLD)
    assert AuditEvent.objects.get(action="app.set_source").organization_id == moves.org.pk


@pytest.mark.parametrize(
    "case,code,message",
    [("outside_installation", "PRECONDITION", OUTSIDE), ("taken", "CONFLICT", TAKEN)],
)
def test_a_refused_rename_leaves_the_app_on_the_old_name_and_audits_why(
    world, github, autowired, case, code, message
):
    app = world.medops_app
    if case == "outside_installation":
        github.installed = [OLD]
    else:
        world.platform_app.source_repo = NEW
        world.platform_app.save()
    before = _snapshot(app)

    resp = _deliver(world.conn, _renamed())

    assert resp.json() == {
        "ok": True,
        "renamed": {"from": OLD, "to": NEW},
        "moved": [],
        "refused": [{"app": app.slug, "reason": code}],
    }
    assert _snapshot(app) == before
    assert autowired == []
    row = AuditEvent.objects.get(action="app.set_source")
    assert (row.actor_kind, row.decision, row.target_id) == ("system", "ALLOW", str(app.guid))
    assert (row.data["error_code"], row.data["error_message"]) == (code, message)
    assert (row.data["previous_source_repo"], row.data["source_repo"], row.data["renamed_to"]) == (
        OLD,
        OLD,
        NEW,
    )


def test_an_app_the_change_fails_for_stays_and_the_others_still_move(world, github, autowired, monkeypatch):
    import astrolift_scm.services.source_change as source_change

    world.platform_app.source_repo = OLD
    world.platform_app.manifest_path = "apps/gateway/astrolift.toml"
    world.platform_app.save()
    listed = source_change.installed_repository
    asked: list[str] = []

    def times_out_once(organization_id, repo):
        asked.append(repo)
        if len(asked) == 1:
            raise TimeoutError("The read operation timed out")
        return listed(organization_id, repo)

    monkeypatch.setattr(source_change, "installed_repository", times_out_once)

    resp = _deliver(world.conn, _renamed())

    assert resp.status_code == 202, resp.content
    assert (resp.json()["moved"], resp.json()["refused"]) == (
        [{"app": world.platform_app.slug, "source_repo": NEW}],
        [{"app": world.medops_app.slug, "reason": "INTERNAL"}],
    )
    world.medops_app.refresh_from_db()
    assert world.medops_app.source_repo == OLD
    row = AuditEvent.objects.get(action="app.set_source", target_id=str(world.medops_app.guid))
    assert (row.data["error_code"], row.data["error_message"], row.data["source_repo"]) == (
        "INTERNAL",
        "The read operation timed out",
        OLD,
    )


def test_apps_being_torn_down_or_not_from_github_stay(world, github, autowired):
    world.medops_app.provisioning_status = RegisteredApp.ProvisioningStatus.TEARING_DOWN.value
    world.medops_app.save()
    world.platform_app.source_kind = "gitlab"
    world.platform_app.source_repo = OLD
    world.platform_app.manifest_path = "apps/gateway/astrolift.toml"
    world.platform_app.save()
    before = (_snapshot(world.medops_app), _snapshot(world.platform_app))

    resp = _deliver(world.conn, _renamed())

    assert resp.json() == {"ok": True, "ignored": "no_matching_app", "repo": OLD}
    assert (_snapshot(world.medops_app), _snapshot(world.platform_app)) == before
    assert (github.calls, autowired) == ([], [])
    assert not AuditEvent.objects.filter(action="app.set_source").exists()


@pytest.mark.parametrize(
    "event,body",
    [
        ("repository", _renamed(action="edited")),
        ("repository", _renamed(changes=False)),
        ("push", _renamed()),
    ],
    ids=["another-action", "no-previous-name", "another-event"],
)
def test_other_deliveries_rename_nothing(world, github, autowired, event, body):
    before = _snapshot(world.medops_app)

    resp = _deliver(world.conn, body, event=event)

    assert resp.json() == {"ok": True, "ignored": "non_push"}
    assert _snapshot(world.medops_app) == before
    assert (github.calls, autowired) == ([], [])


def test_an_unsigned_rename_changes_nothing(world, github, autowired):
    before = _snapshot(world.medops_app)

    resp = _deliver(world.conn, _renamed(), secret="not-the-app-secret")

    assert resp.status_code == 401
    assert _snapshot(world.medops_app) == before
    assert (github.calls, autowired) == ([], [])


def test_a_repeated_delivery_is_not_applied_again(world, github, autowired):
    app = world.medops_app
    assert _deliver(world.conn, _renamed(), delivery="d-7").json()["moved"] == [
        {"app": app.slug, "source_repo": NEW}
    ]
    # Moved back by hand; the same delivery arriving again must not undo that.
    RegisteredApp.objects.filter(pk=app.pk).update(source_repo=OLD)

    resp = _deliver(world.conn, _renamed(), delivery="d-7")

    assert resp.json() == {"ok": True, "ignored": "duplicate", "delivery_id": "d-7"}
    app.refresh_from_db()
    assert app.source_repo == OLD
    assert len(autowired) == 1
    assert WebhookDelivery.objects.filter(
        connection=world.conn, delivery_id="d-7", host_event="repository", repo_full_name=NEW
    ).exists()
