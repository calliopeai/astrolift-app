"""App secrets survive every repo sync and never block one.

setAppSecret, which ``astro app secrets create`` calls, keeps the value in
the top-level ``[env]`` table of the staged manifest buffer; there is no
other store for a literal secret's value. Every repo sync treated that
buffer as an unpushed manifest draft. The deploy-time resync refused with
``diverged`` once the repo text differed from the stored manifest, so the
app stopped picking up its manifest from the repo, and Pull from repo, the
one way out, discarded the buffer and every secret value with it.

Reported on a SteadyMD app whose repo manifest differed from the stored one
only by an ``[environments.*]`` rename. The normalized hash leaves that
table out, so no sync ever stored the new text, and every deploy after a
secret was set came back ``diverged`` with nothing to apply.
"""

from __future__ import annotations

import itertools
import tomllib
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_lifecycle.models import Deployment
from astrolift_manifest.env_edit import read_app_env
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import Workload
from astrolift_registry.schema.mutations import RegistryMutation, SyncManifestFromRepoInput
from astrolift_scm.models import SourceConnection
from astrolift_services.models import SecretChangeProposal
from astrolift_services.schema.mutations import ApproveSecretChangeInput, ServicesMutation
from astrolift_services.schema.mutations.types import DeleteAppSecretInput, SetAppSecretInput
from astrolift_services.secret_literals import literal_secrets_for_environment
from astrolift_workflows.activities.app_lifecycle import _resync_manifest_for_deploy_sync
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_REGISTERED = """\
name = "Hello"

[environments.staging]

[[workloads]]
name = "web"
kind = "deployment"
replicas = 1

[[workloads.containers]]
name = "web"
is_primary = true
port = 3000
"""

# What the repo holds now: the environment renamed. The normalized hash
# leaves [environments.*] out, so this alone changes nothing a deploy renders.
_RENAMED = _REGISTERED.replace("[environments.staging]", "[environments.production]")
# A change a deploy does render.
_SCALED = _RENAMED.replace("replicas = 1", "replicas = 3")

_SECRETS = {
    "LOGIN_LINK_PSK": "psk-value",
    "PARTNER_API_KEY": "partner-key-value",
    "SESSION_NAME_KEY": "session-key-value",
}


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _ctx(app, user=None):
    return tenant_context(
        TenantContext(organization_id=app.organization_id, actor_user_id=user.id if user else None)
    )


@pytest.fixture
def repo(app, monkeypatch):
    """``app`` registered from its repo at ``_REGISTERED``. Set ``repo.text``
    to what the deploy branch holds; ``repo.on_fetch`` runs during a fetch."""
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
    persist_manifest(
        app, normalize(parse_raw(_REGISTERED), defaults=NormalizationDefaults()), raw_text=_REGISTERED
    )
    state = SimpleNamespace(text=_REGISTERED, on_fetch=None)

    def _fetch(connection, repo_full_name, path, ref):  # noqa: ARG001
        if state.on_fetch is not None:
            state.on_fetch()
        return state.text

    monkeypatch.setattr("astrolift_registry.services.manifest_sync._default_fetch", _fetch)
    return state


def _set_secrets(app, secrets: dict[str, str]) -> None:
    """What ``astro app secrets create <key> <app>`` does, once per key."""
    with _ctx(app):
        for key, value in secrets.items():
            result = ServicesMutation().set_app_secret(
                _info(), input=SetAppSecretInput(app_slug=app.slug, key=key, value=value, set_via="cli")
            )
            assert result.ok, result.errors
            assert result.data.pending_proposal_id is None
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


def _pull_from_repo(app) -> None:
    with _ctx(app):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(), input=SyncManifestFromRepoInput(id=str(app.guid))
        )
    assert result.ok, result.errors
    app.refresh_from_db()


def _without_env(text: str) -> dict:
    data = tomllib.loads(text)
    data.pop("env", None)
    return data


def _replicas(app) -> int:
    return Workload.objects.get(registered_app=app, slug="web", deleted_at__isnull=True).replicas


@pytest.mark.parametrize("repo_text", [_RENAMED, _SCALED], ids=["hash-neutral-change", "rendered-change"])
def test_a_deploy_after_setting_secrets_syncs_the_repo_and_ships_the_secrets(
    permission_resolver, app, env, repo, repo_text
):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_secrets(app, _SECRETS)
    repo.text = repo_text

    outcome = _deploy_resync(app, env)

    assert outcome["status"] == ("in_sync" if repo_text == _RENAMED else "applied"), outcome["error"]
    assert app.manifest_raw == repo_text
    assert _replicas(app) == (3 if repo_text == _SCALED else 1)
    # The secrets ride on the new manifest, and the deploy still gets them.
    assert read_app_env(app.manifest_raw_staged) == _SECRETS
    assert _without_env(app.manifest_raw_staged) == _without_env(repo_text)
    assert literal_secrets_for_environment(app, env) == _SECRETS


@pytest.mark.parametrize("repo_text", [_REGISTERED, _RENAMED, _SCALED])
def test_pull_from_repo_keeps_the_secrets(permission_resolver, app, env, repo, repo_text):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_secrets(app, _SECRETS)
    repo.text = repo_text

    _pull_from_repo(app)

    assert app.manifest_raw == repo_text
    assert read_app_env(app.manifest_raw_staged) == _SECRETS
    assert literal_secrets_for_environment(app, env) == _SECRETS


def test_manifest_edits_still_block_a_deploy_and_pull_from_repo_drops_only_them(
    permission_resolver, app, env, repo
):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_secrets(app, _SECRETS)
    # An unpushed edit outside [env], as the Config page's editor stages one.
    app.manifest_raw_staged = app.manifest_raw_staged.replace("replicas = 1", "replicas = 7")
    app.save(update_fields=["manifest_raw_staged"])
    repo.text = _SCALED

    outcome = _deploy_resync(app, env)

    assert outcome["status"] == "diverged"
    assert "app secrets in [env] are kept" in outcome["error"]
    assert "replicas = 7" in app.manifest_raw_staged
    assert _replicas(app) == 1

    _pull_from_repo(app)

    assert _replicas(app) == 3
    assert _without_env(app.manifest_raw_staged) == _without_env(_SCALED)
    assert read_app_env(app.manifest_raw_staged) == _SECRETS


def test_a_deleted_secret_stays_deleted_across_a_sync(permission_resolver, app, env, repo):
    """deleteAppSecret on a key the repo declares stages the removal; a sync
    must not bring the value back."""
    permission_resolver.grant(Permission.APP_UPDATE)
    with_key = f'{_REGISTERED}\n[env]\nOLD_TOKEN = "from-the-repo"\n'
    repo.text = with_key
    _pull_from_repo(app)
    assert literal_secrets_for_environment(app, env) == {"OLD_TOKEN": "from-the-repo"}
    with _ctx(app):
        result = ServicesMutation().delete_app_secret(
            _info(), input=DeleteAppSecretInput(app_slug=app.slug, key="OLD_TOKEN")
        )
    assert result.ok, result.errors
    repo.text = with_key.replace("replicas = 1", "replicas = 3")

    outcome = _deploy_resync(app, env)

    assert outcome["status"] == "applied"
    assert read_app_env(app.manifest_raw) == {"OLD_TOKEN": "from-the-repo"}
    assert literal_secrets_for_environment(app, env) == {}


def test_nothing_stays_staged_once_the_repo_carries_the_same_values(permission_resolver, app, env, repo):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_secrets(app, {"API_KEY": "v1"})
    repo.text = f'{_SCALED}\n[env]\nAPI_KEY = "v1"\n'

    assert _deploy_resync(app, env)["status"] == "applied"

    assert app.manifest_raw_staged == ""
    assert literal_secrets_for_environment(app, env) == {"API_KEY": "v1"}


def test_a_secret_set_while_the_repo_is_fetched_is_kept(permission_resolver, app, env, repo):
    """The SCM fetch takes seconds. A secret set meanwhile lands on the row
    after the deploy loaded it, and must be carried, not written over."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_secrets(app, {"LOGIN_LINK_PSK": "psk-value"})
    repo.text = _SCALED
    repo.on_fetch = lambda: _set_secrets(app, {"PARTNER_API_KEY": "partner-key-value"})

    assert _deploy_resync(app, env)["status"] == "applied"

    assert read_app_env(app.manifest_raw_staged) == {
        "LOGIN_LINK_PSK": "psk-value",
        "PARTNER_API_KEY": "partner-key-value",
    }


_usernames = itertools.count()


def _user():
    n = next(_usernames)
    return get_user_model().objects.create(username=f"sync-user-{n}@test", email=f"sync-user-{n}@test")


def test_a_carried_approval_does_not_override_a_later_repo_change(permission_resolver, app, env, repo):
    """With secret approval on, an approved value is deployed only while the
    key still has the value it was approved against. Carrying the staged
    value across a sync must not change that: the repo's new value wins."""
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    app.requires_secret_approval = True
    app.save(update_fields=["requires_secret_approval"])
    proposer, approver = _user(), _user()
    with _ctx(app, proposer):
        proposed = ServicesMutation().set_app_secret(
            _info(proposer), input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="approved-value")
        )
    assert proposed.ok, proposed.errors
    with _ctx(app, approver):
        approved = ServicesMutation().approve_secret_change(
            _info(approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposed.data.pending_proposal_id)),
        )
    assert approved.ok, approved.errors
    proposal = SecretChangeProposal.objects.get(guid=str(proposed.data.pending_proposal_id))
    assert proposal.status == SecretChangeProposal.Status.APPLIED
    app.refresh_from_db()
    assert literal_secrets_for_environment(app, env) == {"API_KEY": "approved-value"}

    repo.text = f'{_RENAMED}\n[env]\nAPI_KEY = "from-the-repo"\n'
    assert _deploy_resync(app, env)["status"] == "in_sync"

    assert read_app_env(app.manifest_raw_staged) == {"API_KEY": "approved-value"}
    assert literal_secrets_for_environment(app, env) == {"API_KEY": "from-the-repo"}
