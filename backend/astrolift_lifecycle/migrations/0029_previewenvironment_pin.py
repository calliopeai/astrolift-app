"""Persist the preview GC pin on ``PreviewEnvironment`` (#1399).

The GC policy in ``astrolift_workflows/preview_gc.py`` has modelled an
operator pin since #88 — ``is_eligible_for_gc`` short-circuits on it and
max-active eviction filters pinned rows out of the candidate list — but
there was no column to project it from, so the branch was unreachable.

Four additive columns, mirroring the ``webhook_deploys_paused_*`` shape
already on ``RegisteredApp``:

* ``is_pinned`` — the flag the GC policy reads.
* ``pinned_at`` / ``pinned_by`` / ``pin_reason`` — the audit trail, set
  on pin and cleared on unpin.

All four are nullable or defaulted, so this is a SAFE (additive)
migration under ``core/migration_gate.py``; no backfill is required
(``is_pinned`` defaults to False, which is the pre-migration behaviour).
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0028_deployment_github_deployment_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="previewenvironment",
            name="is_pinned",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Operator pin. Pinned previews are never auto-evicted by the "
                    "preview GC — neither by TTL nor by max_active."
                ),
            ),
        ),
        migrations.AddField(
            model_name="previewenvironment",
            name="pinned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="previewenvironment",
            name="pinned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pinned_preview_environments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="previewenvironment",
            name="pin_reason",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
