"""backfill_managed_zone_ids records only verified platform-created zones (#1931)."""

from __future__ import annotations

import uuid
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster

pytestmark = pytest.mark.django_db


@pytest.fixture
def cluster():
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="aws", slug=f"aws-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    return TenantCluster.objects.create(
        name="c",
        slug=f"c-{uuid.uuid4().hex[:6]}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )


def _row(zone, zone_id, cluster):
    return ManagedDomain.objects.create(
        zone=zone, dns_driver="route53", dns_config={"zone_id": zone_id, "provision_cluster_id": cluster.pk}
    )


def test_report_writes_nothing_and_apply_records_only_verified_ids(cluster, monkeypatch):
    platform = {("ours.example", "Z-OURS")}
    driver = SimpleNamespace(zone_created_by_platform=lambda zone, zone_id: (zone, zone_id) in platform)
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda c, cap: driver)
    ours = _row("ours.example", "Z-OURS", cluster)
    tampered = _row("tampered.example", "Z-INSTALL", cluster)

    out = StringIO()
    call_command("backfill_managed_zone_ids", stdout=out)
    ours.refresh_from_db()
    assert ours.provision_zone_id == ""
    assert "1 verified, 1 left unverified" in out.getvalue()

    call_command("backfill_managed_zone_ids", "--apply", stdout=StringIO())
    ours.refresh_from_db()
    tampered.refresh_from_db()
    assert ours.provision_zone_id == "Z-OURS"
    assert tampered.provision_zone_id == ""
