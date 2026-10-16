"""Push to repo never writes app secrets into a pull request (#2411).

setAppSecret, rotateAppSecret, deleteAppSecret, bulkImportAppSecrets and an
approved secret change keep the app's literal secrets, in plaintext, in the
top-level ``[env]`` table of the staged manifest buffer. pushManifestToRepo
wrote that buffer to a branch as it was and opened a pull request, so every
staged secret value landed in git. It now writes the draft with the ``[env]``
table the base branch already has, and leaves the secrets staged, where every
repo sync carries them (#2410).

The secret writes also read and wrote the buffer without the app row lock
the repo sync holds while it rewrites it. A write that read the buffer before
a sync committed put back a draft built on the manifest the sync replaced.
"""

from __future__ import annotations

import itertools
import threading
import time
import tomllib
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection

from astrolift_lifecycle.models import Deployment
from astrolift_manifest.env_edit import app_env_edits, read_app_env
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.mutations import (
    PushManifestToRepoInput,
    RegistryMutation,
    SyncManifestFromRepoInput,
    UpdateManifestInput,
)
from astrolift_registry.services import manifest_sync
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import PullRequestResult
from astrolift_services.models import SecretChangeProposal
from astrolift_services.schema.mutations import ApproveSecretChangeInput, ServicesMutation
from astrolift_services.schema.mutations.types import (
    BulkImportAppSecretsInput,
    DeleteAppSecretInput,
    RotateAppSecretInput,
    SetAppSecretInput,
)
from astrolift_services.secret_literals import literal_secrets_for_environment
from astrolift_workflows.activities.app_lifecycle import _resync_manifest_for_deploy_sync
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_REPO = """\
name = "Hello"

[environments.production]

[[workloads]]
name = "web"
kind = "deployment"
replicas = 1

[[workloads.containers]]
name = "web"
is_primary = true
port = 3000

[env]
LOG_LEVEL = "info"
OLD_TOKEN = "committed-before-astrolift"
"""

_REPO_ENV = {"LOG_LEVEL": "info", "OLD_TOKEN": "committed-before-astrolift"}

# The repo after a commit a deploy renders.
_SCALED = _REPO.replace("replicas = 1", "replicas = 3")

_SECRETS = {
    "LOGIN_LINK_PSK": "psk-value",
    "PARTNER_API_KEY": "partner-key-value",
}

_HEAD = "astrolift/manifest-hello-app"


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _ctx(app, user=None):
    return tenant_context(
        TenantContext(organization_id=app.organization_id, actor_user_id=user.id if user else None)
    )


class _Repo:
    """The source repo as the SCM provider calls see it: a manifest per
    branch, and the writes and pull requests a push makes."""

    def __init__(self, text: str | None):
        self.files: dict[str, str | None] = {"main": text}
        self.fetches: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.pulls: list[dict] = []

    def fetch_file(self, connection, *, repo_full_name, path, ref):  # noqa: ARG002
        self.fetches.append(ref)
        return self.files.get(ref)

    def put_file(self, connection, *, repo_full_name, path, branch, content, commit_message, **_):  # noqa: ARG002
        self.files[branch] = content
        self.writes.append((branch, content))

    def open_pull_request(self, connection, *, repo_full_name, head_branch, base_branch, title, body):  # noqa: ARG002
        self.pulls.append({"head": head_branch, "base": base_branch, "title": title, "body": body})
        return PullRequestResult(
            url=f"https://github.com/acme/hello/pull/{len(self.pulls)}",
            number=str(len(self.pulls)),
            head_branch=head_branch,
            base_branch=base_branch,
        )

    def merge(self) -> None:
        self.files["main"] = self.files[_HEAD]


@pytest.fixture
def repo(app, monkeypatch):
    """``app`` registered from its GitHub repo at ``_REPO``."""
    SourceConnection.objects.create(
        organization=app.organization,
        kind=SourceConnection.Kind.GITHUB_PAT,
        display_name="Acme PAT",
        account_login="acme",
    )
    app.source_kind = "github"
    app.source_repo = "acme/hello"
    app.manifest_path = "astrolift.toml"
    app.deploy_branch = "main"
    app.default_branch = "main"
    app.save(update_fields=["source_kind", "source_repo", "manifest_path", "deploy_branch", "default_branch"])
    persist_manifest(app, normalize(parse_raw(_REPO), defaults=NormalizationDefaults()), raw_text=_REPO)
    state = _Repo(_REPO)
    # The repo sync reaches fetch_file through manifest_sync._default_fetch,
    # the push calls the three directly.
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", state.fetch_file)
    monkeypatch.setattr("astrolift_scm.providers.put_file", state.put_file)
    monkeypatch.setattr("astrolift_scm.providers.open_pull_request", state.open_pull_request)
    return state


def _set_secrets(app, secrets: dict[str, str]) -> None:
    with _ctx(app):
        for key, value in secrets.items():
            result = ServicesMutation().set_app_secret(
                _info(), input=SetAppSecretInput(app_slug=app.slug, key=key, value=value, set_via="cli")
            )
            assert result.ok, result.errors
            assert result.data.pending_proposal_id is None
    app.refresh_from_db()


def _stage_edit(app, old: str, new: str) -> None:
    """A manifest edit outside [env], saved from the Config page's editor."""
    app.refresh_from_db()
    with _ctx(app):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid), raw_manifest=app.manifest_raw_staged.replace(old, new)
            ),
        )
    assert result.ok, result.errors
    app.refresh_from_db()


def _push(app):
    with _ctx(app):
        result = RegistryMutation().push_manifest_to_repo(
            _info(), input=PushManifestToRepoInput(id=str(app.guid))
        )
    app.refresh_from_db()
    return result


def _pull_from_repo(app) -> None:
    with _ctx(app):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(), input=SyncManifestFromRepoInput(id=str(app.guid))
        )
    assert result.ok, result.errors
    app.refresh_from_db()


def _deploy_resync(app, env) -> dict:
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )
    outcome = _resync_manifest_for_deploy_sync(deployment.pk)
    app.refresh_from_db()
    return outcome


def _without_env(text: str) -> dict:
    data = tomllib.loads(text)
    data.pop("env", None)
    return data


def _replicas(app) -> int:
    return Workload.objects.get(registered_app=app, slug="web", deleted_at__isnull=True).replicas


def _platform_secrets(app) -> dict[str, str]:
    """setAppSecret x2, a rotation of a value the repo holds, and the
    deletion of another: every kind of [env] edit the platform stages."""
    _set_secrets(app, {**_SECRETS, "LOG_LEVEL": "debug-set-on-the-platform"})
    with _ctx(app):
        deleted = ServicesMutation().delete_app_secret(
            _info(), input=DeleteAppSecretInput(app_slug=app.slug, key="OLD_TOKEN")
        )
    assert deleted.ok, deleted.errors
    app.refresh_from_db()
    expected = {**_SECRETS, "LOG_LEVEL": "debug-set-on-the-platform"}
    assert read_app_env(app.manifest_raw_staged) == expected
    return expected


def test_push_writes_the_manifest_edits_and_no_secret_value(permission_resolver, app, env, repo):
    permission_resolver.grant(Permission.APP_UPDATE)
    platform_env = _platform_secrets(app)
    _stage_edit(app, "replicas = 1", "replicas = 3")

    result = _push(app)

    assert result.ok, result.errors
    [(branch, written)] = repo.writes
    for value in platform_env.values():
        assert value not in written
    assert result.data.note == "pushed"
    assert result.data.branch_name == branch == _HEAD
    # The [env] written is the one on the pull request's base branch.
    assert repo.fetches == ["main"]
    pushed = tomllib.loads(written)
    # The repo's [env] as it is, the platform's rotation and deletion not
    # carried either; the edit outside [env] is.
    assert pushed["env"] == _REPO_ENV
    assert pushed["workloads"][0]["replicas"] == 3
    [pull] = repo.pulls
    assert "does not carry the `[env]` changes staged there" in pull["body"]

    # The secrets stay staged and deployable.
    assert read_app_env(app.manifest_raw_staged) == platform_env
    assert literal_secrets_for_environment(app, env) == platform_env


def test_the_secrets_survive_merging_the_pushed_pull_request(permission_resolver, app, env, repo):
    permission_resolver.grant(Permission.APP_UPDATE)
    platform_env = _platform_secrets(app)
    _stage_edit(app, "replicas = 1", "replicas = 3")
    assert _push(app).ok
    repo.merge()

    _pull_from_repo(app)

    assert app.manifest_raw == repo.files["main"]
    assert _replicas(app) == 3
    assert read_app_env(app.manifest_raw_staged) == platform_env
    # Only the secrets are left staged, so the next deploy syncs cleanly
    # and a second push has nothing to send.
    assert app_env_edits(app.manifest_raw, app.manifest_raw_staged).other_changes is False
    assert _deploy_resync(app, env)["status"] == "in_sync"
    assert literal_secrets_for_environment(app, env) == platform_env
    assert _push(app).data.note == "env_only"
    assert len(repo.writes) == 1


def test_a_draft_holding_only_secrets_has_nothing_to_push(permission_resolver, app, repo):
    permission_resolver.grant(Permission.APP_UPDATE)
    platform_env = _platform_secrets(app)

    result = _push(app)

    assert result.ok, result.errors
    assert result.data.note == "env_only"
    assert result.data.pr_url == ""
    assert (repo.fetches, repo.writes, repo.pulls) == ([], [], [])
    assert read_app_env(app.manifest_raw_staged) == platform_env


@pytest.mark.parametrize(
    "repo_text",
    [_REPO.split("[env]")[0], None, "name = [not toml"],
    ids=["repo-has-no-env", "repo-has-no-manifest", "repo-manifest-unreadable"],
)
def test_the_env_pushed_is_the_base_branchs_not_the_stored_manifests(
    permission_resolver, app, repo, repo_text
):
    """manifest_raw can hold [env] values git never had: a manifest typed
    into the registration wizard, or one applied while the repo could not be
    read (#2296). The base branch decides what [env] the pull request has."""
    permission_resolver.grant(Permission.APP_UPDATE)
    stored = f'{_REPO.split("[env]")[0]}[env]\nWIZARD_TOKEN = "typed-into-the-wizard"\n'
    app.manifest_raw = stored
    app.save(update_fields=["manifest_raw"])
    repo.files["main"] = repo_text
    _set_secrets(app, _SECRETS)
    _stage_edit(app, "replicas = 1", "replicas = 3")

    result = _push(app)

    assert result.ok, result.errors
    [(_, written)] = repo.writes
    for value in ("typed-into-the-wizard", *_SECRETS.values()):
        assert value not in written
    assert "env" not in tomllib.loads(written)
    assert tomllib.loads(written)["workloads"][0]["replicas"] == 3


def test_a_draft_that_does_not_parse_is_not_pushed(permission_resolver, app, repo):
    permission_resolver.grant(Permission.APP_UPDATE)
    app.manifest_raw_staged = 'name = "Hello"\n[env]\nAPI_KEY = "secret-value"\nbroken = [\n'
    app.save(update_fields=["manifest_raw_staged"])

    result = _push(app)

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "secret-value" not in result.errors[0].message
    assert repo.writes == []


def test_the_diverged_message_points_at_pull_from_repo_not_a_push(permission_resolver, app, env, repo):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_secrets(app, _SECRETS)
    _stage_edit(app, "replicas = 1", "replicas = 7")
    repo.files["main"] = _SCALED

    outcome = _deploy_resync(app, env)

    assert outcome["status"] == "diverged"
    assert "Pull from repo" in outcome["error"]
    assert "app secrets in [env] are kept" in outcome["error"]
    assert "Push the draft" not in outcome["error"]


# The race: a deploy-time sync holds the app row, having written the
# synced manifest and the secrets carried onto it but not yet committed.
# A secret write starts then. Unlocked, it read the committed row (the
# old manifest), waited on the row only for its UPDATE, and once the sync
# committed wrote back a buffer built on the old manifest: the repo's
# change was reverted in the draft, and a push would have reverted it in
# the repo. Locked, it waits before reading and lands on the synced one.


def _lock_waiters() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        return cursor.fetchone()[0]


def _on_own_connection(fn) -> tuple[threading.Thread, dict]:
    box: dict = {}

    def run():
        close_old_connections()
        try:
            box["result"] = fn()
        except BaseException as exc:
            box["error"] = exc
        finally:
            close_old_connections()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


_users = itertools.count()


def _user():
    n = next(_users)
    return get_user_model().objects.create(username=f"push-2411-{n}@test", email=f"push-2411-{n}@test")


def _in_tenant(app, user, call):
    with _ctx(app, user):
        return call(_info(user))


def _proposal(app, proposer, call) -> str:
    result = _in_tenant(app, proposer, call)
    assert result.ok, result.errors
    assert result.data.pending_proposal_id is not None
    return str(result.data.pending_proposal_id)


_RACING_WRITES = {
    "set": (
        lambda info, app: ServicesMutation().set_app_secret(
            info, input=SetAppSecretInput(app_slug=app.slug, key="NEW_KEY", value="new-value")
        ),
        {**_REPO_ENV, **_SECRETS, "NEW_KEY": "new-value"},
    ),
    "rotate": (
        lambda info, app: ServicesMutation().rotate_app_secret(
            info, input=RotateAppSecretInput(app_slug=app.slug, key="PARTNER_API_KEY", value="rotated-value")
        ),
        {**_REPO_ENV, **_SECRETS, "PARTNER_API_KEY": "rotated-value"},
    ),
    "delete": (
        lambda info, app: ServicesMutation().delete_app_secret(
            info, input=DeleteAppSecretInput(app_slug=app.slug, key="LOGIN_LINK_PSK")
        ),
        {**_REPO_ENV, "PARTNER_API_KEY": "partner-key-value"},
    ),
    "bulk_import": (
        lambda info, app: ServicesMutation().bulk_import_app_secrets(
            info, input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text="NEW_KEY=new-value\n")
        ),
        {**_REPO_ENV, **_SECRETS, "NEW_KEY": "new-value"},
    ),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("write", [*_RACING_WRITES, "approved_set", "approved_delete"])
def test_a_secret_write_during_a_sync_lands_on_the_synced_manifest(
    permission_resolver, app, env, repo, monkeypatch, write
):
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    _set_secrets(app, _SECRETS)
    if write.startswith("approved_"):
        # Secret approval on: the write is the approval applying a proposal.
        app.requires_secret_approval = True
        app.save(update_fields=["requires_secret_approval"])
        proposer, approver = _user(), _user()
        propose, expected = _RACING_WRITES["set" if write == "approved_set" else "delete"]
        proposal_id = _proposal(app, proposer, lambda info: propose(info, app))

        def perform():
            return _in_tenant(
                app,
                approver,
                lambda info: ServicesMutation().approve_secret_change(
                    info, input=ApproveSecretChangeInput(proposal_id=proposal_id)
                ),
            )

    else:
        call, expected = _RACING_WRITES[write]

        def perform():
            return _in_tenant(app, None, lambda info: call(info, app))

    repo.files["main"] = _SCALED
    synced, release = threading.Event(), threading.Event()
    reconcile = manifest_sync._reconcile_with_repo

    def reconcile_and_hold(*args, **kwargs):
        result = reconcile(*args, **kwargs)
        synced.set()
        release.wait(timeout=30)
        return result

    monkeypatch.setattr(manifest_sync, "_reconcile_with_repo", reconcile_and_hold)

    syncer, sync_box = _on_own_connection(
        lambda: manifest_sync.resync_app_manifest_from_repo(RegisteredApp.objects.get(pk=app.pk))
    )
    assert synced.wait(timeout=30)
    writer, write_box = _on_own_connection(perform)
    deadline = time.monotonic() + 30
    while writer.is_alive() and not _lock_waiters() and time.monotonic() < deadline:
        time.sleep(0.05)
    release.set()
    syncer.join(timeout=30)
    writer.join(timeout=30)

    assert "error" not in sync_box, sync_box
    assert "error" not in write_box, write_box
    assert sync_box["result"].status == "applied"
    assert write_box["result"].ok, write_box["result"].errors
    app.refresh_from_db()
    assert app.manifest_raw == _SCALED
    assert read_app_env(app.manifest_raw_staged) == expected
    assert _without_env(app.manifest_raw_staged) == _without_env(_SCALED)
    if write.startswith("approved_"):
        assert (
            SecretChangeProposal.objects.get(guid=proposal_id).status == SecretChangeProposal.Status.APPLIED
        )
