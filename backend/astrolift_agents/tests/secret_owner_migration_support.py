"""Typed agent secrets cannot cross a sibling project, team or shared owner."""

import pytest

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.services.secret_owner_namespaces import agent_secret_prefix
from core.tests.utils.scope_world import ScopeWorld, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_index(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))


class Store:
    def __init__(self):
        self.data = {}
        self.calls = []

    def get(self, key):
        self.calls.append(("get", key))
        return self.data.get(key)

    def upsert(self, key, value):
        self.calls.append(("upsert", key))
        self.data[key] = dict(value)

    def delete(self, key):
        self.calls.append(("delete", key))
        self.data.pop(key, None)


@pytest.fixture
def world(monkeypatch):
    w = ScopeWorld("2102")
    w.user = make_user("2102")
    w.info = make_info(w.user)
    w.cluster = make_cluster(w, "2102")
    w.cluster.lifecycle = "managed"
    w.cluster.region = "us-west-2"
    w.cluster.save()
    w.specs = []
    for slug, team, project in [
        ("own", w.medops, w.medops_project),
        ("sibling", w.platform, w.platform_project),
        ("team", w.medops, None),
        ("shared", None, None),
    ]:
        spec = AgentEnvironmentSpec.objects.create(
            organization=w.org, team=team, project=project, slug=slug, name=slug, agent_type="claude"
        )
        spec.secret_refs = [{"env_var": "TOKEN", "uri": agent_secret_prefix(spec) + "credential"}]
        spec.save()
        w.specs.append(spec)
    w.own, w.sibling, w.team_spec, w.shared = w.specs
    w.store = Store()
    for spec in w.specs:
        w.store.data[spec.secret_refs[0]["uri"]] = {"value": spec.slug + "-private"}
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: w.store)
    return w
