"""
backfill_managed_zone_ids — record ``provision_zone_id`` for managed domains
provisioned before the column existed (#1931).

Usage:
    python manage.py backfill_managed_zone_ids            # report only
    python manage.py backfill_managed_zone_ids --apply    # write verified ids

``provision_zone_id`` pins every DNS operation and is the only zone teardown
may delete, so it must name a zone the platform created for the row. The
candidate is the row's ``dns_config.zone_id``, which tenants can edit, so it
is written only when the DNS driver confirms that hosted zone carries the
row's name and the CallerReference ``provision_zone`` set. Anything else is
reported and left empty: such a row keeps working for record writes, and
its teardown leaves the hosted zone to the operator.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Record provision_zone_id for managed domains whose platform-created hosted zone can be verified."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", default=False, help="Write verified ids.")

    def handle(self, *args, **options):
        from astrolift_clusters.models import ManagedDomain, TenantCluster
        from core.app_deploy import driver_for_capability

        apply = options["apply"]
        prefix = "" if apply else "[REPORT] "
        rows = ManagedDomain.objects.filter(provision_zone_id="").order_by("pk")
        counts = {"recorded": 0, "unverified": 0, "skipped": 0}
        drivers: dict[int, object] = {}
        for row in rows:
            config = dict(row.dns_config or {})
            zone_id = str(config.get("zone_id") or "")
            cluster_id = config.get("provision_cluster_id")
            if not zone_id or not cluster_id:
                self.stdout.write(f"  SKIP  {row.zone}: no recorded zone id or provisioning cluster")
                counts["skipped"] += 1
                continue
            try:
                if cluster_id not in drivers:
                    cluster = TenantCluster.objects.select_related("provider_plugin").get(pk=cluster_id)
                    drivers[cluster_id] = driver_for_capability(cluster, "dns")
                verify = getattr(drivers[cluster_id], "zone_created_by_platform", None)
                verified = bool(verify and verify(row.zone, zone_id))
            except Exception as exc:  # noqa: BLE001 - report the row, keep going
                self.stdout.write(f"  ERROR {row.zone}: {exc}")
                counts["unverified"] += 1
                continue
            if not verified:
                self.stdout.write(
                    f"  LEAVE {row.zone}: {zone_id} is not a platform-created zone of that name"
                )
                counts["unverified"] += 1
                continue
            self.stdout.write(f"{prefix}  SET   {row.zone}: provision_zone_id={zone_id}")
            if apply:
                row.provision_zone_id = zone_id
                row.save(update_fields=["provision_zone_id", "updated_at", "version"])
            counts["recorded"] += 1
        self.stdout.write(
            f"{prefix}{counts['recorded']} verified, {counts['unverified']} left unverified, "
            f"{counts['skipped']} without a candidate"
        )
