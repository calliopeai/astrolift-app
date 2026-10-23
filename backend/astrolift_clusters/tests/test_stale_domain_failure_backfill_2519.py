"""Migration 0029 clears the failure a later run left behind (#2519).

Runs the migration's own function against the historical model state, as the
#1866 owner backfill test does, rather than a full ``migrate`` round trip.
"""

from __future__ import annotations

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from astrolift_clusters.models import ManagedDomain

pytestmark = pytest.mark.django_db

NAME = "0029_clear_stale_domain_failures"
FAILURE = ("provision_dns_zone", "zone creation failed (ConflictError)")


def _clear() -> None:
    migration = importlib.import_module(f"astrolift_clusters.migrations.{NAME}")
    historical = MigrationExecutor(connection).loader.project_state(("astrolift_clusters", NAME)).apps
    migration.clear_stale_failures(historical, None)


def _domain(zone: str, state: str) -> ManagedDomain:
    step, reason = FAILURE
    return ManagedDomain.objects.create(
        zone=zone,
        dns_driver="route53",
        provision_state=state,
        provision_failed_step=step,
        provision_failure_reason=reason,
    )


def _failure(domain: ManagedDomain) -> tuple[str, str]:
    domain.refresh_from_db()
    return domain.provision_failed_step, domain.provision_failure_reason


def test_a_zone_a_later_run_moved_on_loses_the_earlier_failure():
    """astro.conflict.cloud: active, still reporting the run before."""
    active = _domain("astro.example.com", "mark_active")
    polling = _domain("polling.example.com", "configure_cert_policy")

    _clear()

    assert _failure(active) == ("", "")
    assert _failure(polling) == ("", "")


def test_a_zone_still_stalled_keeps_its_failure():
    stalled = _domain("stalled.example.com", "failed")

    _clear()

    assert _failure(stalled) == FAILURE
