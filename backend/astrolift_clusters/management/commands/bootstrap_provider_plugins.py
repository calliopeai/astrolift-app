"""
``manage.py bootstrap_provider_plugins``

Seeds (or re-syncs) the ``ProviderPlugin`` catalog rows that the cluster
registration form joins against. Without rows, every call to
``registerTenantCluster`` fails the
``ProviderPlugin.objects.filter(slug=…).first()`` lookup in
``astrolift_clusters/schema/mutations.py`` and the Clusters page shows
"No clusters registered" forever even after the operator submits the
form — that's a silent UX trap, so we make sure the catalog mirrors
whatever ``astrolift.providers`` entry points the image actually ships.

Idempotent / upsert-style: safe to run on every container start. Pair
with ``bootstrap_admin`` + ``bootstrap_idp`` in ``ON_STARTUP``. Source
of truth is the in-process plugin registry, populated at Django boot by
``astrolift_clusters.plugin_loader.discover_and_register``.

Inputs: none. Reads from ``plugins.list()`` and writes one
``ProviderPlugin`` row per loaded plugin. Each row's
``capabilities_manifest`` captures which driver roles the plugin
implements so the cluster validator can reject a registration that
needs a capability the plugin doesn't ship (the spec calls for this in
``02-multi-cloud-k8s-abstraction.md`` §2).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from astrolift_clusters.models import ProviderPlugin
from astrolift_clusters.plugin_loader import discover_and_register
from astrolift_drivers.registry import plugins


class Command(BaseCommand):
    help = "Upsert ProviderPlugin catalog rows from the in-process plugin registry."

    def handle(self, *args, **options) -> None:
        # ready() should have populated the registry already, but a fresh
        # database hits this command before any web request — re-run the
        # discovery so the catalog reflects whatever the image ships.
        discover_and_register()

        loaded = plugins.list()
        if not loaded:
            self.stdout.write("No provider plugins loaded; nothing to upsert.")
            return

        with transaction.atomic():
            for manifest in loaded:
                roles = sorted(
                    role
                    for role in manifest.drivers
                    # Synthetic 'managed:<kind>:<variant>' roles are useful
                    # in-process but noisy in the catalog manifest. Roll
                    # them up into a single 'managed_service' capability
                    # and let the UI introspect them through a separate
                    # GraphQL query when it cares.
                    if not role.startswith("managed:")
                )
                managed_kinds = sorted(
                    {role.split(":", 2)[1] for role in manifest.drivers if role.startswith("managed:")}
                )
                capabilities = {
                    "drivers": roles,
                    "managed_service_kinds": managed_kinds,
                }

                # ProviderPlugin.version is a CharField ("0.0.0") that
                # shadows TrackingMixin.version (the optimistic-lock
                # IntegerField). BaseCoreModel.save() does
                # ``self.version = (self.version or 0) + 1`` and crashes
                # with TypeError when version is a string. Upstream's
                # test fixture in 2853cd3 uses bulk_create to skirt the
                # collision; do the same here — bulk_create + .update()
                # both bypass .save(). Right long-term fix is to rename
                # the semver field on ProviderPlugin (e.g. plugin_version)
                # so it stops colliding with the mixin's int version.
                row_fields = {
                    "name": manifest.display_name,
                    "capabilities_manifest": capabilities,
                    "is_enabled": True,
                    "deleted_at": None,
                    "deleted_by": None,
                }
                existing = ProviderPlugin.all_objects.filter(slug=manifest.plugin_id).first()
                if existing is None:
                    ProviderPlugin.all_objects.bulk_create(
                        [
                            ProviderPlugin(
                                slug=manifest.plugin_id,
                                version=manifest.version,
                                **row_fields,
                            )
                        ]
                    )
                    action = "created"
                else:
                    # Don't overwrite the CharField semver on update — the
                    # operator may have stamped it themselves. Touch only
                    # the catalog-derived fields.
                    ProviderPlugin.all_objects.filter(pk=existing.pk).update(**row_fields)
                    action = "updated"

                self.stdout.write(
                    f"  {action} provider plugin {manifest.plugin_id!r} "
                    f"({len(roles)} drivers, {len(managed_kinds)} managed kinds)"
                )

        self.stdout.write(self.style.SUCCESS(f"Upserted {len(loaded)} provider plugin row(s)."))
