"""Add ``organization`` FK to ``DataProcess`` (#542).

Closes the tenant-isolation leak surfaced by the #537 audit-sweep:
``DataProcessType.get_queryset`` used to return the unfiltered table
(model-wide permission only), which leaked every tenant's import rows
to anyone with ``view_dataprocess``.

Migration shape:

1. Add a nullable ``organization`` FK to ``DataProcess`` and the
   simple-history shadow ``HistoricalDataProcess``. Nullable so the
   backfill step can leave rows with no resolvable org as NULL —
   :class:`DataProcessType` denies-by-default for those rows.
2. Backfill: prefer the upload's organization (the upload the import
   was created from already carries the resolved tenant). Fall back to
   ``created_by.profile.active_organization`` for stranded rows.
   Anything still NULL after the backfill is dead-on-arrival from the
   resolver's perspective — that's by design.
3. Add the (organization, status) compound index that
   ``DataProcessType.get_queryset`` will hit on every list query.

Stays nullable on the column so the data-migration step doesn't
double-lock the table on a sloppy fresh-clone deploy and so that
``DataProcess.objects.create`` calls outside the upload flow (the
API-data-import path that has no Upload row) can still land without
forcing every callsite to resolve a tenant up-front.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def _backfill_organization(apps, schema_editor):
    DataProcess = apps.get_model("core", "DataProcess")
    Upload = apps.get_model("core", "Upload")
    Profile = apps.get_model("core", "Profile")

    # First pass: pull org from the upload the DataProcess was created
    # from. This is the common case — anyone who used the GraphQL
    # upload flow.
    upload_org = dict(
        Upload.objects.exclude(organization_id__isnull=True).values_list("id", "organization_id")
    )
    if upload_org:
        rows = (
            DataProcess.objects.filter(organization_id__isnull=True)
            .exclude(uploaded_file_id__isnull=True)
            .values_list("pk", "uploaded_file_id")
        )
        for pk, uploaded_file_id in rows:
            org_id = upload_org.get(uploaded_file_id)
            if org_id is not None:
                DataProcess.objects.filter(pk=pk).update(organization_id=org_id)

    # Second pass: rows with no upload (API-data-imports) fall back to
    # the creator's active organization. ``Profile.active_organization``
    # is the user's pinned tenant.
    rows = DataProcess.objects.filter(organization_id__isnull=True).exclude(
        created_by_id__isnull=True
    ).values_list("pk", "created_by_id")
    if rows:
        creator_org = dict(
            Profile.objects.filter(
                user_id__in={uid for _, uid in rows},
                active_organization_id__isnull=False,
            ).values_list("user_id", "active_organization_id")
        )
        for pk, user_id in rows:
            org_id = creator_org.get(user_id)
            if org_id is not None:
                DataProcess.objects.filter(pk=pk).update(organization_id=org_id)


def _noop_reverse(apps, schema_editor):
    # Forward sets organization; reverse drops the column entirely, so
    # the data step has no work on the way down.
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_add_mutation_audit_log"),
        ("organization", "0002_historicalorganization_historicalorganizationmember"),
    ]

    operations = [
        migrations.AddField(
            model_name="dataprocess",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Owning organization; filters DataProcessType.get_queryset (#542).",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="data_processes",
                to="organization.organization",
            ),
        ),
        migrations.AddField(
            model_name="historicaldataprocess",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text="Owning organization; filters DataProcessType.get_queryset (#542).",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="organization.organization",
            ),
        ),
        migrations.RunPython(_backfill_organization, _noop_reverse),
        migrations.AddIndex(
            model_name="dataprocess",
            index=models.Index(
                fields=["organization", "status"],
                name="core_datapr_organiz_3fadcb_idx",
            ),
        ),
    ]
