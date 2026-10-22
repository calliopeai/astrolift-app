"""Changing an app's source repository (#2478).

``setAppSource`` follows a repository renamed on GitHub, or moves an app to
another repository, and the wiring follows: autowire runs against the new
repository, so the CI workflow, the CI push role's trust and the CI secrets
name it. GitHub is a fake ``urlopen``; the repo-wiring steps are recorded
where they would write to GitHub. Everything else is real rows.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from astrolift_operations.models import AuditEvent
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations.source import _followed_source_url
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.secrets import PushSecretsResult, ValidateCiSecretsResult
from astrolift_scm.services.webhooks import InstallSourceWebhookResult
from astrolift_scm.services.workflow_sync import WorkflowSyncResult
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

OLD = "ConflictHQ/fossilhub"
NEW = "ConflictHQ/fossilrepos"

NOT_GITHUB = "only an app registered from a GitHub repository can change its repository"
OUTSIDE = (
    f"{NEW} is not in the GitHub App installation on ConflictHQ. "
    "Give the App access to the repository on GitHub, then retry."
)
NOT_A_NAME = f"sourceRepo must be a GitHub repository written as owner/name, not 'https://github.com/{NEW}'"
TAKEN = "another app in this organization is registered from this repository and manifest path"
POLICY = (
    "'elsewhere/fossilrepos' is owned by 'elsewhere', which is not a connected organization. "
    "This install only accepts repositories owned by: conflicthq."
)

SET_APP_SOURCE = """
mutation($input: SetAppSourceInput!) {
  setAppSource(input: $input) {
    ok
    errors { code field message }
    data {
      previousSourceRepo
      app { sourceRepo sourceUrl autowire { connected ciWorkflow webhook secrets detail } }
    }
  }
}
"""


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


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
    """The App installation's repositories, and GitHub's numeric ids for the
    new repository. Every request is recorded."""
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
        if url.endswith(f"/repos/{NEW}"):
            return _Response({"id": 4242, "owner": {"id": 77}, "full_name": NEW})
        raise AssertionError(f"unexpected GitHub call {url}")

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", urlopen)
    return state


def _record_wiring(monkeypatch, calls, *, workflow=True):
    """Record the repository each repo-wiring step ran against, in place of
    its writes to GitHub."""

    def step(name, result):
        def run(app, *args, **kwargs):
            calls.append((name, app.source_repo))
            return result

        return run

    if workflow:
        monkeypatch.setattr(
            "astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo",
            step("workflow", WorkflowSyncResult(status="updated")),
        )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.reconcile_source_webhook_state", step("reconcile", "app_delivers")
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.install_astrolift_source_webhook",
        step("webhook", InstallSourceWebhookResult(status="app_delivers")),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.push_astrolift_ci_secrets",
        step(
            "secrets",
            PushSecretsResult(ok=True, secret_names=("ASTROLIFT_DEPLOY_TOKEN",), new_token_last_4="9f3a"),
        ),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.validate_astrolift_ci_secrets",
        step("validate", ValidateCiSecretsResult(ok=True, results=())),
    )


@pytest.fixture
def wiring(monkeypatch):
    calls: list[tuple[str, str]] = []
    _record_wiring(monkeypatch, calls)
    return calls


@pytest.fixture
def world():
    world = ScopeWorld("source2478")
    world.user = make_user("source2478")
    app = world.medops_app
    app.source_kind = "github"
    app.source_repo = OLD
    app.source_url = f"https://github.com/{OLD}"
    app.autowire_state = {"connected": True, "ci_workflow": "ok", "webhook": "ok", "secrets": "ok"}
    app.save()
    world.platform_app.source_repo = "ConflictHQ/gateway"
    world.platform_app.save()
    encrypted = encrypt_at_rest(b"fake-pem")
    SourceConnection.objects.create(
        organization=world.org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        account_login="ConflictHQ",
        installation_id="987",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    return world


def _grant(world, permission=Permission.APP_UPDATE):
    bind_role(world.user, permissions=[permission], kind="TEAM", scope_id=world.medops.pk, slug="caller")


def _set_source(world, app, source_repo, **fields):
    from config.schema import schema

    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    ):
        result = schema.execute_sync(
            SET_APP_SOURCE,
            variable_values={"input": {"id": str(app.guid), "sourceRepo": source_repo, **fields}},
            context_value=make_info(world.user).context,
        )
    assert result.errors is None, result.errors
    return result.data["setAppSource"]


def _snapshot(app):
    app.refresh_from_db()
    return (app.source_repo, app.source_url, app.autowire_state, app.version)


def test_a_renamed_repository_is_followed_and_the_wiring_moves_with_it(world, github, wiring):
    _grant(world)
    app = world.medops_app

    out = _set_source(world, app, "conflicthq/FOSSILREPOS", ifMatchVersion=app.version)

    assert out["ok"], out["errors"]
    assert out["data"] == {
        "previousSourceRepo": OLD,
        "app": {
            "sourceRepo": NEW,
            "sourceUrl": f"https://github.com/{NEW}",
            "autowire": {
                "connected": True,
                "ciWorkflow": "ok",
                "webhook": "ok",
                "secrets": "ok",
                "detail": "",
            },
        },
    }
    # Stored as GitHub lists it: the App webhook receiver matches deliveries
    # on the exact name.
    assert RegisteredApp.objects.get(organization=world.org, source_repo=NEW) == app
    assert wiring == [
        ("workflow", NEW),
        ("reconcile", NEW),
        ("webhook", NEW),
        ("secrets", NEW),
        ("validate", NEW),
    ]
    row = AuditEvent.objects.get(action="app.set_source")
    assert (row.decision, row.target_kind, row.target_id) == ("ALLOW", "RegisteredApp", str(app.guid))
    assert (row.data["previous_source_repo"], row.data["source_repo"]) == (OLD, NEW)


def test_the_ci_push_role_is_trusted_for_the_new_repository(world, github, monkeypatch):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    calls: list[tuple[str, str]] = []
    _record_wiring(monkeypatch, calls, workflow=False)
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS", slug="aws", plugin_version="0.0.1", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    app = world.medops_app
    app.default_tenant_cluster = TenantCluster.objects.create(
        organization=world.org,
        name="prod",
        slug="prod-source2478",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app.registry_repo_uri = f"111111111111.dkr.ecr.us-west-2.amazonaws.com/{world.org.slug}/{app.slug}"
    app.save()
    trusted: list[tuple] = []

    class _Registry:
        def ensure_ci_push_role(self, *, repo, scm_provider, scm_repo_full_name, scm_repo_numeric_ids=None):
            trusted.append((repo, scm_repo_full_name, scm_repo_numeric_ids))
            return SimpleNamespace(role_ref="arn:aws:iam::111111111111:role/push")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: _Registry())
    # The real workflow sync runs; only its write to the repository is GitHub's.
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._sync_github",
        lambda app, force_pr=False: calls.append(("workflow", app.source_repo))
        or WorkflowSyncResult(status="updated"),
    )
    _grant(world)

    out = _set_source(world, app, NEW)

    assert out["ok"], out["errors"]
    assert trusted == [(f"{world.org.slug}/{app.slug}", NEW, (77, 4242))]
    assert calls[0] == ("workflow", NEW)
    app.refresh_from_db()
    assert app.push_role_ref == "arn:aws:iam::111111111111:role/push"
    assert app.ci_workflow_state["path"].startswith(".github/workflows/")


def test_the_change_stands_when_autowire_fails_and_the_old_verdicts_are_gone(world, github, monkeypatch):
    def fail(app, *, actor=None):
        raise RuntimeError("worker lost")

    monkeypatch.setattr("astrolift_registry.schema.mutations.source.run_autowire", fail)
    _grant(world)

    out = _set_source(world, world.medops_app, NEW)

    assert out["ok"], out["errors"]
    assert out["data"]["app"]["sourceRepo"] == NEW
    autowire = out["data"]["app"]["autowire"]
    assert (autowire["ciWorkflow"], autowire["webhook"], autowire["secrets"]) == (
        "missing",
        "missing",
        "missing",
    )
    world.medops_app.refresh_from_db()
    assert (world.medops_app.source_repo, world.medops_app.autowire_state) == (NEW, {})


def test_a_registration_racing_the_change_is_a_conflict_not_an_internal_error(world, monkeypatch, wiring):
    import astrolift_registry.schema.mutations.source as source

    def raced(organization_id, repo):
        RegisteredApp.objects.filter(pk=world.platform_app.pk).update(source_repo=NEW)
        return NEW

    monkeypatch.setattr(source, "installed_repository", raced)
    _grant(world)
    app = world.medops_app
    before = _snapshot(app)

    out = _set_source(world, app, NEW)

    assert out["errors"] == [{"code": "CONFLICT", "field": "sourceRepo", "message": TAKEN}]
    assert _snapshot(app) == before
    assert wiring == []


def test_an_unchanged_repository_is_left_alone(world, github, wiring):
    _grant(world)
    app = world.medops_app
    before = _snapshot(app)

    out = _set_source(world, app, f"  {OLD} ")

    assert out["ok"], out["errors"]
    assert (out["data"]["previousSourceRepo"], out["data"]["app"]["sourceRepo"]) == (OLD, OLD)
    assert _snapshot(app) == before
    assert github.calls == []
    assert wiring == []


def test_the_same_repository_for_another_manifest_or_organization_is_not_taken(world, github, wiring):
    _grant(world)
    world.platform_app.source_repo = NEW
    world.platform_app.manifest_path = "apps/gateway/astrolift.toml"
    world.platform_app.save()
    foreign = ScopeWorld("source2478other")
    foreign.medops_app.source_repo = NEW
    foreign.medops_app.save()

    out = _set_source(world, world.medops_app, NEW)

    assert out["ok"], out["errors"]
    world.medops_app.refresh_from_db()
    assert world.medops_app.source_repo == NEW


def test_a_name_registered_in_other_case_takes_githubs_spelling(world, github, wiring):
    # Typed by hand at registration. The App receiver matches GitHub's
    # spelling exactly, so no push ever found this app.
    app = world.medops_app
    app.source_repo = OLD.lower()
    app.save()
    github.installed = [OLD]
    _grant(world)

    out = _set_source(world, app, OLD.upper())

    assert out["ok"], out["errors"]
    assert (out["data"]["previousSourceRepo"], out["data"]["app"]["sourceRepo"]) == (OLD.lower(), OLD)
    assert [repo for _, repo in wiring] == [OLD] * 5


WORKFLOW = """\
[workflow]
slug = "fossil-triage"
name = "Fossil Triage"
pattern = "chained"

[[stage]]
kind = "agent_dispatch"
agent = "fossil-agent"
prompt = "Collect evidence."
output_key = "evidence"
"""
TRIAGE = {"workflows/triage.toml": WORKFLOW}
INTAKE = {"workflows/intake.toml": WORKFLOW.replace("fossil-triage", "fossil-intake")}


def _reconcile_workflows(world, repo, files=TRIAGE):
    """Reconcile ``workflows/`` the way an agent registration, push or
    "Resync from source" under ``repo`` does."""
    from workflows.repo_sync import discover_repository_workflows, reconcile_repository_workflows

    return reconcile_repository_workflows(
        organization=world.org,
        project=world.medops_project,
        source_repo=repo,
        source_ref="main",
        manifests=discover_repository_workflows(files),
    )


def _workflow_owners(world):
    """Each definition's repository, retired ones included."""
    from workflows.models import WorkflowDefinition

    return dict(WorkflowDefinition.objects.filter(organization=world.org).values_list("slug", "source_repo"))


def test_the_repositorys_workflows_follow_the_last_app_off_the_old_name(world, github, wiring):
    _reconcile_workflows(world, OLD, {**TRIAGE, **INTAKE})
    _reconcile_workflows(world, OLD)  # retires fossil-intake
    _grant(world)

    out = _set_source(world, world.medops_app, NEW)

    assert out["ok"], out["errors"]
    assert _workflow_owners(world) == {"fossil-triage": NEW, "fossil-intake": NEW}
    # The next sync under the new name updates the definition in place. It
    # used to refuse, the old name still holding the slug, and so did every
    # sync and "Resync from source" after it.
    assert [(row.slug, row.created) for row in _reconcile_workflows(world, NEW)] == [("fossil-triage", False)]


def test_the_workflows_stay_while_another_app_names_the_old_repository(world, github, wiring):
    world.platform_app.source_repo = OLD
    world.platform_app.manifest_path = "apps/gateway/astrolift.toml"
    world.platform_app.save()
    _reconcile_workflows(world, OLD)
    _grant(world)

    out = _set_source(world, world.medops_app, NEW)

    assert out["ok"], out["errors"]
    assert _workflow_owners(world) == {"fossil-triage": OLD}


def test_another_organizations_apps_and_workflows_on_the_old_name_are_not_counted_or_moved(
    world, github, wiring
):
    foreign = ScopeWorld("source2478workflows")
    foreign.medops_app.source_repo = OLD
    foreign.medops_app.save()
    _reconcile_workflows(foreign, OLD)
    _reconcile_workflows(foreign, NEW, INTAKE)
    _reconcile_workflows(world, OLD)
    _grant(world)

    out = _set_source(world, world.medops_app, NEW)

    assert out["ok"], out["errors"]
    assert (_workflow_owners(world), _workflow_owners(foreign)) == (
        {"fossil-triage": NEW},
        {"fossil-triage": OLD, "fossil-intake": NEW},
    )


def test_the_workflows_are_not_merged_into_a_repository_that_declares_its_own(world, github, wiring):
    _reconcile_workflows(world, OLD)
    _reconcile_workflows(world, NEW, INTAKE)
    _grant(world)

    out = _set_source(world, world.medops_app, NEW)

    assert out["ok"], out["errors"]
    assert _workflow_owners(world) == {"fossil-triage": OLD, "fossil-intake": NEW}


@pytest.mark.parametrize(
    "case,source_repo,error,asks_github",
    [
        ("outside_installation", NEW, ("PRECONDITION", "sourceRepo", OUTSIDE), True),
        ("url", f"https://github.com/{NEW}", ("VALIDATION", "sourceRepo", NOT_A_NAME), False),
        ("taken", NEW, ("CONFLICT", "sourceRepo", TAKEN), False),
        ("policy", "elsewhere/fossilrepos", ("VALIDATION", "sourceRepo", POLICY), False),
        ("tearing_down", NEW, ("PRECONDITION", None, "the app is being torn down"), False),
        ("not_github", NEW, ("PRECONDITION", "sourceRepo", NOT_GITHUB), False),
        ("no_repository", NEW, ("PRECONDITION", "sourceRepo", NOT_GITHUB), False),
    ],
)
def test_a_refused_change_writes_nothing_and_wires_nothing(
    world, github, wiring, monkeypatch, case, source_repo, error, asks_github
):
    _grant(world)
    app = world.medops_app
    if case == "outside_installation":
        github.installed = [OLD]
    elif case == "taken":
        world.platform_app.source_repo = "conflicthq/FossilRepos"
        world.platform_app.save()
    elif case == "policy":
        monkeypatch.setattr(
            "constance.config", SimpleNamespace(RESTRICT_SOURCE_REPOS_TO_ORG=True), raising=False
        )
    elif case == "tearing_down":
        app.provisioning_status = RegisteredApp.ProvisioningStatus.TEARING_DOWN.value
        app.save()
    elif case == "not_github":
        app.source_kind = "gitlab"
        app.save()
    elif case == "no_repository":
        app.source_repo = ""
        app.save()
    before = _snapshot(app)

    out = _set_source(world, app, source_repo)

    code, field, message = error
    assert out == {"ok": False, "errors": [{"code": code, "field": field, "message": message}], "data": None}
    assert _snapshot(app) == before
    assert wiring == []
    assert bool(github.calls) is asks_github


def test_a_stale_version_is_refused_before_anything_else(world, github, wiring):
    _grant(world)
    app = world.medops_app
    before = _snapshot(app)

    out = _set_source(world, app, NEW, ifMatchVersion=app.version - 1)

    assert [error["code"] for error in out["errors"]] == ["VERSION_MISMATCH"]
    assert _snapshot(app) == before
    assert (github.calls, wiring) == ([], [])


@pytest.mark.parametrize(
    "permission,target",
    [(Permission.APP_READ, "medops_app"), (Permission.APP_UPDATE, "platform_app")],
    ids=["read-only-on-own-app", "update-on-sibling-team-app"],
)
def test_a_caller_without_app_update_on_the_app_is_refused(world, github, wiring, permission, target):
    _grant(world, permission)
    app = getattr(world, target)
    before = _snapshot(app)

    out = _set_source(world, app, NEW)

    assert [error["code"] for error in out["errors"]] == ["PERMISSION_DENIED"]
    assert _snapshot(app) == before
    assert (github.calls, wiring) == ([], [])
    assert AuditEvent.objects.get(action="app.set_source").decision == "DENY"


def test_another_organizations_app_is_not_found(world, github, wiring):
    foreign = ScopeWorld("source2478foreign")
    foreign.medops_app.source_repo = OLD
    foreign.medops_app.save()
    bind_role(world.user, permissions=[Permission.APP_UPDATE], kind="ORG", scope_id=world.org.pk, slug="org")
    before = _snapshot(foreign.medops_app)

    out = _set_source(world, foreign.medops_app, NEW)

    assert [error["code"] for error in out["errors"]] == ["NOT_FOUND"]
    assert _snapshot(foreign.medops_app) == before
    assert (github.calls, wiring) == ([], [])


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://github.com/{OLD}", f"https://github.com/{NEW}"),
        ("https://github.com/conflicthq/FossilHub.git/", f"https://github.com/{NEW}"),
        (f"https://ghe.example.test/{OLD}", f"https://ghe.example.test/{NEW}"),
        ("https://docs.example.test/fossil", "https://docs.example.test/fossil"),
        (f"https://github.com/{OLD}/tree/main", f"https://github.com/{OLD}/tree/main"),
        ("", ""),
    ],
)
def test_the_source_url_follows_only_when_it_named_the_old_repository(url, expected):
    assert _followed_source_url(url, OLD, NEW) == expected
