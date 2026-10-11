"""Environments that still share a namespace are reported, never moved (#2094)."""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.utils import timezone

from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.services.shared_namespaces import shared_environment_namespaces
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db


def test_two_legacy_environments_on_one_cluster_are_reported_together():
    world = ScopeWorld("shared-ns-2094")
    cluster = make_cluster(world, "shared-ns-2094")
    for name in ("production", "staging"):
        AppEnvironment.objects.create(registered_app=world.medops_app, tenant_cluster=cluster, name=name)

    [row] = shared_environment_namespaces(organization_slug=world.org.slug)

    assert row.app == world.medops_app.slug and row.cluster == cluster.slug
    assert row.environments == ("production", "staging")


def test_own_namespace_other_cluster_and_deleted_environments_are_not_shared():
    world = ScopeWorld("own-ns-2094")
    cluster = make_cluster(world, "own-ns-2094")
    other = make_cluster(world, "own-ns-2094-b")
    AppEnvironment.objects.create(registered_app=world.medops_app, tenant_cluster=cluster, name="production")
    AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=cluster,
        name="staging",
        k8s_namespace="medops-staging-2094",
    )
    AppEnvironment.objects.create(registered_app=world.medops_app, tenant_cluster=other, name="qa")
    AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=cluster, name="old", deleted_at=timezone.now()
    )

    assert shared_environment_namespaces(organization_slug=world.org.slug) == []


def test_the_command_prints_json_and_exits_nonzero_only_when_something_is_shared(capsys):
    world = ScopeWorld("cmd-ns-2094")
    cluster = make_cluster(world, "cmd-ns-2094")
    AppEnvironment.objects.create(registered_app=world.medops_app, tenant_cluster=cluster, name="production")

    call_command("report_shared_environment_namespaces", organization=world.org.slug)
    assert "No environments share a namespace." in capsys.readouterr().out

    AppEnvironment.objects.create(registered_app=world.medops_app, tenant_cluster=cluster, name="staging")
    with pytest.raises(SystemExit) as exc:
        call_command("report_shared_environment_namespaces", organization=world.org.slug, json=True)
    assert exc.value.code == 1
    [row] = json.loads(capsys.readouterr().out)
    assert row["environments"] == ["production", "staging"]
