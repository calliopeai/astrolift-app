"""Moving a shared environment into its own namespace, step by step (#2094)."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.services import environment_namespace_move as move
from astrolift_lifecycle.services.shared_namespaces import shared_environment_namespaces
from core.app_deploy import namespace_for_app, namespace_for_environment
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db


@pytest.fixture
def shared():
    world = ScopeWorld("move-ns-2094")
    cluster = make_cluster(world, "move-ns-2094")
    production = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=cluster, name="production"
    )
    staging = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=cluster, name="staging"
    )
    world.cluster, world.production, world.staging = cluster, production, staging
    world.target = f"{world.org.slug}/{world.medops_app.slug}"
    return world


class Cluster:
    def __init__(self, workloads=None):
        self.workloads = workloads or {}
        self.listed, self.deleted = [], []

    def list_manifests(self, cluster, namespace, kind):
        self.listed.append((cluster, namespace, kind))
        return self.workloads.get(kind, [])

    def delete_manifests(self, cluster, namespace, manifests):
        self.deleted.append((cluster, namespace, manifests))


def test_the_plan_keeps_the_primary_and_changes_nothing(shared, capsys):
    call_command("move_environment_namespace", shared.target, "staging")

    out = capsys.readouterr().out
    app_ns = namespace_for_app(shared.medops_app)
    assert f"staging moves from {app_ns} to {app_ns}-staging" in out and "production keeps" in out
    shared.staging.refresh_from_db()
    assert shared.staging.k8s_namespace == ""


def test_apply_records_the_namespace_and_ends_the_sharing(shared):
    call_command("move_environment_namespace", shared.target, "staging", apply=True)

    shared.staging.refresh_from_db()
    assert namespace_for_environment(shared.staging) == f"{namespace_for_app(shared.medops_app)}-staging"
    assert namespace_for_environment(shared.production) == namespace_for_app(shared.medops_app)
    assert shared_environment_namespaces(organization_slug=shared.org.slug) == []


@pytest.mark.parametrize(
    "environment, message",
    [
        ("production", "is the primary and keeps the app namespace; move staging instead"),
        ("missing", "has no live environment"),
    ],
)
def test_the_primary_and_unknown_environments_are_refused(shared, environment, message):
    with pytest.raises(CommandError, match=message):
        call_command("move_environment_namespace", shared.target, environment, apply=True)
    shared.production.refresh_from_db()
    assert shared.production.k8s_namespace == ""


def test_a_moved_or_unshared_environment_is_refused(shared):
    call_command("move_environment_namespace", shared.target, "staging", apply=True)
    with pytest.raises(CommandError, match="already renders into its own namespace"):
        call_command("move_environment_namespace", shared.target, "staging", apply=True)
    with pytest.raises(CommandError, match="only environment in its namespace"):
        call_command("move_environment_namespace", shared.target, "production")


def test_a_plan_overtaken_by_another_change_is_not_recorded(shared):
    plan = move.plan_move(shared.org.slug, shared.medops_app.slug, "staging")
    AppEnvironment.objects.filter(pk=shared.staging.pk).update(k8s_namespace="elsewhere-2094")

    with pytest.raises(move.MoveRefused):
        move.record_move(plan)
    shared.staging.refresh_from_db()
    assert shared.staging.k8s_namespace == "elsewhere-2094"


def test_clean_up_waits_until_the_primary_owns_the_app_namespace_again(shared, monkeypatch):
    call_command("move_environment_namespace", shared.target, "staging", apply=True)
    secret = move._literal_secret(shared.staging)
    stale = {
        "metadata": {"name": "web"},
        "spec": {"template": {"spec": {"containers": [{"envFrom": [{"secretRef": {"name": secret}}]}]}}},
    }
    cluster = Cluster({"apps/v1/Deployment": [stale]})
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda _cluster: cluster)

    with pytest.raises(CommandError, match="Deployment/web .* still reference .*redeploy the primary"):
        call_command("move_environment_namespace", shared.target, "staging", clean_up=True, apply=True)
    assert cluster.deleted == []

    cluster.workloads = {"apps/v1/Deployment": [{"metadata": {"name": "web"}, "spec": {"replicas": 1}}]}
    call_command("move_environment_namespace", shared.target, "staging", clean_up=True)
    assert cluster.deleted == []
    call_command("move_environment_namespace", shared.target, "staging", clean_up=True, apply=True)

    [(cluster_slug, namespace, [deleted])] = cluster.deleted
    assert (cluster_slug, namespace) == (shared.cluster.slug, namespace_for_app(shared.medops_app))
    assert deleted["kind"] == "Secret" and deleted["metadata"]["name"] == secret


def test_clean_up_before_the_move_is_refused(shared, monkeypatch):
    monkeypatch.setattr(
        "core.cluster_management._driver_for_cluster",
        lambda _cluster: pytest.fail("cluster read before a move"),
    )
    with pytest.raises(CommandError, match="has not been moved"):
        call_command("move_environment_namespace", shared.target, "staging", clean_up=True, apply=True)
