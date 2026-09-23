"""App-side reads of an org-level secret bundle stay in the org's namespace (#1921).

An org-level bundle's ``backendRef`` is free text typed through the agent
surface, and one stored before #1921 could name another tenant's secret.
Attaching it to an app must not carry that secret into the app's namespace
through a deploy, a rotation, or the key-count refresh. Team bundles (no
surface creates them) and project bundles (the platform pins their path) keep
resolving wherever they point.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import Deployment
from astrolift_services.models import AppSecretBundleRef, SecretBundle
from astrolift_workflows.activities.app_lifecycle import _update_secrets_sync
from astrolift_workflows.activities.secret_rotation import _list_targets_sync, _refresh_in_cluster_sync
from core.app_deploy import AppDeployError
from providers._sdk.cluster import ApplyResult

pytestmark = pytest.mark.django_db

# The AWS driver resolves this to astrolift/managed/rds-orders/credentials:
# another tenant's database credentials.
_VICTIM = "managed/rds-orders/credentials"


class _Store:
    """In-memory SecretsBackend that records every path it is asked for."""

    def __init__(self, store):
        self.store = dict(store)
        self.reads: list[str] = []

    def get(self, path):
        self.reads.append(path)
        return self.store.get(path)

    def list_keys(self, path):
        self.reads.append(path)
        return sorted(self.store.get(path) or {})


class _ClusterDriver:
    def __init__(self):
        self.applied: list[dict] = []

    def apply_manifests(self, cluster_slug, namespace, manifests):
        self.applied.extend(manifests)
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])


@pytest.fixture
def drivers(monkeypatch):
    store = _Store({})
    cluster_driver = _ClusterDriver()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda _cluster, _capability: store)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda _deployment: (cluster_driver, SimpleNamespace(slug="dev-cluster"), "apps"),
    )
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda _cluster: SimpleNamespace(slug="dev-cluster")
    )
    return SimpleNamespace(store=store, cluster=cluster_driver)


def _attach(app, env, *, organization, backend_ref, slug="db-bundle", team=None):
    bundle = SecretBundle.objects.create(
        organization=organization, team=team, name=slug, slug=slug, backend_ref=backend_ref
    )
    AppSecretBundleRef.objects.create(registered_app=app, app_environment=env, secret_bundle=bundle)
    return bundle


def _deploy(app, env):
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.PENDING,
        image_tag="bundle-namespace-test",
    )
    return _update_secrets_sync(deployment.pk)


# ---- deploy (update_secrets) -------------------------------------------------


def test_deploy_refuses_an_org_bundle_outside_the_org_namespace(org, app, env, drivers):
    _attach(app, env, organization=org, backend_ref=_VICTIM)
    drivers.store.store[_VICTIM] = {"PASSWORD": "victim-pw"}

    with pytest.raises(AppDeployError, match=f"agent-bundles/{org.guid}/"):
        _deploy(app, env)

    assert drivers.store.reads == []
    assert drivers.cluster.applied == []


def test_deploy_materializes_an_org_bundle_inside_the_org_namespace(org, app, env, drivers):
    location = f"agent-bundles/{org.guid}/db-bundle"
    _attach(app, env, organization=org, backend_ref=location)
    drivers.store.store[location] = {"PASSWORD": "own-pw"}

    assert _deploy(app, env) == 1
    assert drivers.store.reads == [location]


def test_deploy_keeps_materializing_a_team_bundle(org, team, app, env, drivers):
    _attach(app, env, organization=org, team=team, backend_ref="vault/db")
    drivers.store.store["vault/db"] = {"PASSWORD": "team-pw"}

    assert _deploy(app, env) == 1
    assert drivers.store.reads == ["vault/db"]


# ---- rotation (refresh_secret_bundle_in_cluster) ------------------------------


def test_rotation_refuses_an_org_bundle_outside_the_org_namespace(org, app, env, drivers):
    bundle = _attach(app, env, organization=org, backend_ref=_VICTIM)
    drivers.store.store[_VICTIM] = {"PASSWORD": "victim-pw"}
    [target] = _list_targets_sync(bundle.pk)

    with pytest.raises(AppDeployError, match=f"agent-bundles/{org.guid}/"):
        _refresh_in_cluster_sync(target)

    assert drivers.store.reads == []
    assert drivers.cluster.applied == []


def test_rotation_writes_the_key_cache_onto_this_orgs_bundle_only(org, team, app, env, drivers):
    """Another org's bundle can carry the same slug and ref (team bundles are
    free text); the refreshed key names belong to the bundle this app has
    attached, not to whichever row was created first."""
    other = Organization.objects.create(name="Globex", slug="globex-1921")
    other_team = Team.objects.create(organization=other, name="Globex Eng", slug="globex-eng")
    theirs = SecretBundle.objects.create(
        organization=other, team=other_team, name="db-bundle", slug="db-bundle", backend_ref="vault/db"
    )
    ours = _attach(app, env, organization=org, team=team, backend_ref="vault/db")
    drivers.store.store["vault/db"] = {"PASSWORD": "pw", "USER": "app"}
    [target] = _list_targets_sync(ours.pk)

    _refresh_in_cluster_sync(target)

    ours.refresh_from_db()
    theirs.refresh_from_db()
    assert ours.last_known_keys == ["PASSWORD", "USER"]
    assert theirs.last_known_keys == []


# ---- key-count refresh ---------------------------------------------------------


def test_key_refresh_never_lists_an_org_bundle_outside_the_org_namespace(org, app, env):
    from astrolift_services.bundle_keys import refresh_bundle_known_keys

    bundle = _attach(app, env, organization=org, backend_ref=_VICTIM)
    store = _Store({_VICTIM: {"PASSWORD": "victim-pw"}})

    assert refresh_bundle_known_keys(bundle, secrets_backend=store) == []

    bundle.refresh_from_db()
    assert bundle.last_known_keys == []
    assert store.reads == []
