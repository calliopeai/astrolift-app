"""Destructive-migration ratchet: run the gate over the real migration graph.

``core.migration_gate`` was written for this ("CI runs the check on every
PR; the deploy workflow runs it again before applying") and then never
called by anything but its own unit tests, so a migration that drops a
column has always been able to land unremarked.

This wires it to the actual on-disk migrations. Migrations that were
already merged when the gate went in are grandfathered - they are applied
in every environment, so blocking them now would only break the suite. New
destructive migrations fail here and have to be added to
``GRANDFATHERED_DESTRUCTIVE`` deliberately, in a diff a reviewer sees.

Adding an entry is not a rubber stamp. ``RemoveField`` and ``DeleteModel``
drop data that no rollback restores; the point of the list is that
somebody chose to accept that, in writing.
"""

from __future__ import annotations

import pathlib

import pytest
from django.apps import apps
from django.db.migrations.loader import MigrationLoader

from core.migration_gate import (
    DestructiveMigrationBlocked,
    MigrationRisk,
    assess_operations,
    gate,
    render_preview,
)

# ``app_label/migration_name`` for every destructive migration that had
# already merged when this ratchet landed.
GRANDFATHERED_DESTRUCTIVE: frozenset[str] = frozenset(
    {
        "astrolift_agents/0023_agentbox_drop_session_name",
        "astrolift_billing/0006_remove_costsnapshot_cost_snapshot_unique_per_day_scope_and_more",
        "astrolift_ci/0002_delete_ci_models",
        "astrolift_lifecycle/0021_previewenvironment_is_manual",
        "astrolift_pipelines/0004_remove_runner_runner_org_status_idx_and_more",
        "astrolift_registry/0034_scope_repo_manifest_unique_to_organization",
        "astrolift_services/0014_managedserviceattachment_and_more",
        "astrolift_services/0018_managedservicevolumebinding_dynamic_pvc",
        # Replaces the app-wide uniqueness constraint with an environment-
        # scoped constraint. Constraint metadata is dropped and recreated;
        # no table rows or columns are removed.
        "astrolift_services/0023_manifest_managed_services",
        "core/0005_remove_domain_specific_profile_fields",
        "core/0010_drop_metabase",
        "core/0011_delete_historicalmetabasechart",
        "workflows/0004_historicalworkflow_workflow_and_more",
        "workflows/0007_org_slug_unique_live_only",
    }
)


def _first_party_labels() -> frozenset[str]:
    """App labels whose code lives in this repo. Django's own apps ship
    destructive migrations (``contenttypes/0002_remove_content_type_name``)
    that we neither own nor can revise, so the gate does not judge them."""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    labels = set()
    for config in apps.get_app_configs():
        try:
            path = pathlib.Path(config.path).resolve()
        except (OSError, ValueError):
            continue
        if path.is_relative_to(repo_root) and "site-packages" not in path.parts:
            labels.add(config.label)
    return frozenset(labels)


def _assessed_migrations() -> dict[str, object]:
    """Every first-party migration on disk, assessed. No DB connection:
    ``MigrationLoader`` with ``connection=None`` reads the graph off the
    filesystem."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    first_party = _first_party_labels()
    return {
        f"{app_label}/{name}": assess_operations(migration.operations)
        for (app_label, name), migration in loader.disk_migrations.items()
        if app_label in first_party
    }


def test_the_ratchet_actually_sees_the_migration_graph():
    """Vacuity guard. A loader that returned nothing would make every other
    assertion here pass over an empty set."""
    assessed = _assessed_migrations()
    assert len(assessed) > 100, f"only {len(assessed)} migrations found; the loader is not reading the graph"
    assert (
        "contenttypes/0002_remove_content_type_name" not in assessed
    ), "third-party migrations leaked into the gate; it can only judge migrations we own"
    assert GRANDFATHERED_DESTRUCTIVE, "an empty grandfather list makes the anti-rot test vacuous"


def test_no_new_destructive_migration_lands_without_review():
    offenders = []
    for key, assessment in sorted(_assessed_migrations().items()):
        try:
            gate(
                assessment=assessment,
                allow_destructive=key in GRANDFATHERED_DESTRUCTIVE,
                migration_label=key,
            )
        except DestructiveMigrationBlocked:
            offenders.append(f"{key}\n{render_preview(assessment)}")

    assert not offenders, (
        "destructive migration(s) not on the reviewed list:\n\n"
        + "\n\n".join(offenders)
        + "\n\nThese drop data that no rollback restores. If the drop is "
        "intended, add the key to GRANDFATHERED_DESTRUCTIVE in this file so "
        "the decision is visible in review."
    )


def test_the_grandfathered_list_only_holds_still_destructive_migrations():
    """Anti-rot: a squashed or deleted migration should leave the list, or it
    quietly licenses a future migration that reuses the name."""
    assessed = _assessed_migrations()
    stale = [
        key
        for key in sorted(GRANDFATHERED_DESTRUCTIVE)
        if key not in assessed or assessed[key].overall != MigrationRisk.DESTRUCTIVE
    ]
    assert not stale, (
        "GRANDFATHERED_DESTRUCTIVE holds entries that are no longer destructive "
        f"migrations on disk: {stale}. Remove them."
    )


@pytest.mark.parametrize("key", sorted(GRANDFATHERED_DESTRUCTIVE))
def test_each_grandfathered_entry_names_a_real_migration(key: str):
    assert key in _assessed_migrations(), f"{key} is not a migration on disk"


def test_an_ungrandfathered_destructive_migration_is_blocked():
    """The ratchet's whole point, proved against a real migration rather than
    a synthetic one. Every destructive migration on disk is currently on the
    list, so without this the gate call above could be passing vacuously -
    it would look identical if ``gate`` never raised at all."""
    assessed = _assessed_migrations()
    sample = "core/0010_drop_metabase"
    assert assessed[sample].overall == MigrationRisk.DESTRUCTIVE

    with pytest.raises(DestructiveMigrationBlocked):
        gate(assessment=assessed[sample], allow_destructive=False, migration_label=sample)
