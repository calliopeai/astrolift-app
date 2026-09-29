"""``manage.py bootstrap_managed_domain`` -- the apps zone, from the installer.

A fresh install came up with no managed domain, so every app it deployed got
no platform hostname until an operator registered the zone by hand. The
installer already owns that zone (it is dedicated to the install, delegated,
and carries the install's wildcard certificate), so it hands it over here:

  ASTROLIFT_MANAGED_DOMAIN_ZONE             the zone, e.g. apps.example.net
  ASTROLIFT_MANAGED_DOMAIN_ZONE_ID          its hosted zone id
  ASTROLIFT_MANAGED_DOMAIN_CERTIFICATE_ARN  the wildcard certificate for it

The row is written in the shape ``ProvisionManagedDomainWorkflow`` leaves once
a zone is active, as the platform default for tenant apps. It needs no TXT
proof: the installer was given the zone by its owner.

Create-only. A zone that already has a row is left exactly as it is, so an
operator's later edits survive every restart, and a silent no-op when the
zone is unset. Runs on every container start (``startup.py``).
"""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand

from astrolift_clusters.dns_layout import ZoneRegistrationStep
from astrolift_clusters.models import ManagedDomain
from astrolift_clusters.schema.mutations import canonical_zone


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


class Command(BaseCommand):
    help = "Register the install's apps zone from the environment, once."

    def handle(self, *args, **options):
        zone = canonical_zone(_env("ASTROLIFT_MANAGED_DOMAIN_ZONE"))
        if not zone:
            return
        if ManagedDomain.objects.filter(zone__iexact=zone, deleted_at__isnull=True).exists():
            self.stdout.write(f"managed domain {zone!r} already registered; left as is")
            return
        ManagedDomain.objects.create(
            organization=None,
            zone=zone,
            dns_driver=_env("ASTROLIFT_CLUSTER_PLUGIN_SLUG") or "aws",
            dns_config={
                "zone_id": _env("ASTROLIFT_MANAGED_DOMAIN_ZONE_ID"),
                "certificate_arn": _env("ASTROLIFT_MANAGED_DOMAIN_CERTIFICATE_ARN"),
            },
            is_wildcard_managed=True,
            default_for=ManagedDomain.DefaultFor.TENANT_APPS,
            provision_state=ZoneRegistrationStep.MARK_ACTIVE.value,
            provision_zone_id=_env("ASTROLIFT_MANAGED_DOMAIN_ZONE_ID"),
            provision_cert_id=_env("ASTROLIFT_MANAGED_DOMAIN_CERTIFICATE_ARN"),
            verification_state=ManagedDomain.VerificationState.NOT_REQUIRED,
        )
        self.stdout.write(self.style.SUCCESS(f"registered managed domain {zone!r} for tenant apps"))
