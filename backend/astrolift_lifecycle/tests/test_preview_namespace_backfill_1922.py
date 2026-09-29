"""Migration 0041 moves existing previews into their own namespace (#1922).

Only an environment with a PreviewEnvironment row is touched. Everything
else keeps a blank ``k8s_namespace``, so production and every other
environment that existed before the migration renders where it did.

Runs the migration's own function against the historical model state, the
pattern the #1919 backfill test uses, so the managers are the ones a real
``migrate`` hands it.
"""

from __future__ import annotations

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_registry.models import RegisteredApp

pytestmark = pytest.mark.django_db

NAME = "0041_backfill_preview_environment_namespace"


def _run():
    migration = importlib.import_module(f"astrolift_lifecycle.migrations.{NAME}")
    historical = MigrationExecutor(connection).loader.project_state(("astrolift_lifecycle", NAME)).apps
    migration.record_preview_namespaces(historical, None)


def _preview(app, cluster, *, env_name, namespace, pr_number=None, **env_fields):
    preview_env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name=env_name, **env_fields
    )
    row = PreviewEnvironment.objects.create(
        registered_app=app,
        pr_number=pr_number,
        is_manual=pr_number is None,
        branch=env_name,
        hostname=f"{env_name}.hello-app.acme-test",
        namespace=namespace,
        app_environment=preview_env,
    )
    return preview_env, row


def test_a_preview_takes_its_recorded_namespace_and_production_keeps_the_app_namespace(app, env, cluster):
    preview_env, row = _preview(
        app, cluster, env_name="preview-pr-4", namespace="acme-test-hello-app-pr-4", pr_number=4
    )

    _run()

    preview_env.refresh_from_db()
    env.refresh_from_db()
    row.refresh_from_db()
    assert preview_env.k8s_namespace == "acme-test-hello-app-pr-4"
    assert row.namespace == "acme-test-hello-app-pr-4"
    assert env.k8s_namespace == ""


def test_a_namespace_two_previews_computed_goes_to_the_first(app, env, cluster):
    """A manual preview of branch ``pr-3`` and PR #3 computed one name and
    both provisioned it. The first keeps it; the second moves to a fresh
    one, and its preview row follows so its teardown deletes that."""
    first_env, first_row = _preview(
        app, cluster, env_name="preview-pr-3", namespace="acme-test-hello-app-pr-3", pr_number=3
    )
    second_env, second_row = _preview(
        app, cluster, env_name="preview-branch", namespace="acme-test-hello-app-pr-3"
    )

    _run()

    for row in (first_env, first_row, second_env, second_row):
        row.refresh_from_db()
    assert first_env.k8s_namespace == first_row.namespace == "acme-test-hello-app-pr-3"
    assert second_env.k8s_namespace == second_row.namespace
    assert second_env.k8s_namespace.startswith("acme-test-hello-app-pr-3-")


def test_a_preview_namespace_another_app_holds_is_not_reused(app, env, cluster, org, project, team):
    RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="recorded",
        slug="hello-app-pr-6",
        k8s_namespace="acme-test-hello-app-pr-6",
    )
    RegisteredApp.objects.create(
        organization=org, project=project, team=team, name="computed", slug="hello-app-pr-8", k8s_namespace=""
    )
    recorded_env, recorded_row = _preview(
        app, cluster, env_name="preview-pr-6", namespace="acme-test-hello-app-pr-6", pr_number=6
    )
    computed_env, _ = _preview(
        app, cluster, env_name="preview-pr-8", namespace="acme-test-hello-app-pr-8", pr_number=8
    )

    _run()

    recorded_env.refresh_from_db()
    computed_env.refresh_from_db()
    recorded_row.refresh_from_db()
    assert recorded_env.k8s_namespace.startswith("acme-test-hello-app-pr-6-")
    assert recorded_row.namespace == recorded_env.k8s_namespace
    assert computed_env.k8s_namespace.startswith("acme-test-hello-app-pr-8-")


def test_a_deleted_environment_is_left_alone_and_still_holds_its_namespace(app, env, cluster):
    gone_env, _ = _preview(
        app, cluster, env_name="preview-pr-1", namespace="acme-test-hello-app-pr-1", pr_number=1
    )
    gone_env.soft_delete()
    live_env, _ = _preview(app, cluster, env_name="preview-again", namespace="acme-test-hello-app-pr-1")

    _run()

    gone_env.refresh_from_db()
    live_env.refresh_from_db()
    assert gone_env.k8s_namespace == ""
    assert live_env.k8s_namespace.startswith("acme-test-hello-app-pr-1-")


def test_a_soft_deleted_preview_row_still_names_its_environments_namespace(app, env, cluster):
    preview_env, row = _preview(
        app, cluster, env_name="preview-pr-2", namespace="acme-test-hello-app-pr-2", pr_number=2
    )
    row.soft_delete()

    _run()

    preview_env.refresh_from_db()
    assert preview_env.k8s_namespace == "acme-test-hello-app-pr-2"


def test_a_preview_row_without_a_namespace_changes_nothing(app, env, cluster):
    preview_env, _ = _preview(app, cluster, env_name="preview-pr-5", namespace="", pr_number=5)

    _run()

    preview_env.refresh_from_db()
    assert preview_env.k8s_namespace == ""


def test_a_second_run_changes_nothing(app, env, cluster):
    first_env, _ = _preview(
        app, cluster, env_name="preview-pr-3", namespace="acme-test-hello-app-pr-3", pr_number=3
    )
    second_env, _ = _preview(app, cluster, env_name="preview-branch", namespace="acme-test-hello-app-pr-3")
    _run()
    before = dict(AppEnvironment.all_objects.values_list("pk", "k8s_namespace"))
    rows_before = dict(PreviewEnvironment.all_objects.values_list("pk", "namespace"))

    _run()

    assert dict(AppEnvironment.all_objects.values_list("pk", "k8s_namespace")) == before
    assert dict(PreviewEnvironment.all_objects.values_list("pk", "namespace")) == rows_before
