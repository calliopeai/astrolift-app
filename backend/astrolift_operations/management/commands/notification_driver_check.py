"""
notification_driver_check -- check an organization's push driver (#2256).

Usage:
    python manage.py notification_driver_check --organization <slug>

Builds the driver the dispatcher would send that organization's pushes with,
from its active NotificationProfile, prints the directory its
``*_secret_ref`` files are read from, and runs the driver's health check:
``apns`` loads the key and signs a provider token, ``gcp_fcm`` mints an
access token from its service-account key. Nothing is sent to a device.

Exits non-zero when the organization has no active profile, the driver
cannot be built (a missing or unreadable secret file, a malformed key) or the
health check fails. Run it in a web or worker pod: they are the processes
that read the secret files.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Build an organization's push notification driver and run its health check; sends nothing."

    def add_arguments(self, parser):
        parser.add_argument("--organization", required=True, help="The organization's slug.")

    def handle(self, *args, **options):
        from astrolift_identity.models import Organization
        from astrolift_operations.models import NotificationProfile
        from astrolift_operations.notification_dispatch import (
            _build_driver_from_profile,
            notification_secrets_dir,
        )

        organization = Organization.objects.filter(slug=options["organization"]).first()
        if organization is None:
            raise CommandError(f"no organization {options['organization']!r}")
        profile = (
            NotificationProfile.objects.filter(organization=organization, is_active=True)
            .order_by("-updated_at")
            .first()
        )
        if profile is None:
            raise CommandError(f"organization {organization.slug} has no active notification profile")

        self.stdout.write(f"organization: {organization.slug} ({organization.guid})")
        self.stdout.write(f"driver: {profile.driver}")
        self.stdout.write(f"secrets directory: {notification_secrets_dir(organization.pk)}")
        try:
            driver = _build_driver_from_profile(
                driver_slug=profile.driver,
                config=profile.config or {},
                organization_id=organization.pk,
            )
        except Exception as exc:  # noqa: BLE001 -- every build failure is the answer here
            raise CommandError(f"the driver cannot be built: {exc}") from exc
        health = driver.healthcheck()
        self.stdout.write(f"health: {'ok' if health.ok else 'failing'} -- {health.message}")
        if not health.ok:
            raise CommandError("the driver's health check failed")
