"""Add ``ttl_until`` to ``PreviewEnvironment`` (#431).

A new column with a callable default; existing rows are backfilled to
``created_at + 7 days`` via a RunPython forward step so the
auto-teardown sweep can interpret historical rows immediately
without waiting for the next deploy to touch them.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import migrations, models

from astrolift_lifecycle.models.preview_environment import _default_ttl_until


def _backfill_ttl(apps, schema_editor):
    """Set ``ttl_until`` to ``created_at + 7d`` on existing rows.

    Without this every row would get the AddField default
    (``now + 7d``) — meaning a preview created weeks ago would
    suddenly look fresh and survive the next GC sweep. Anchoring on
    ``created_at`` preserves the original retention contract.
    """
    PreviewEnvironment = apps.get_model("astrolift_lifecycle", "PreviewEnvironment")
    for row in PreviewEnvironment.objects.all().only("id", "created_at"):
        PreviewEnvironment.objects.filter(pk=row.pk).update(
            ttl_until=row.created_at + timedelta(days=7),
        )


def _noop(apps, schema_editor):
    """Reverse path — the column drop already removes the data."""


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0011_deployment_approval_decision_context"),
    ]

    operations = [
        migrations.AddField(
            model_name="previewenvironment",
            name="ttl_until",
            field=models.DateTimeField(
                default=_default_ttl_until,
                help_text=(
                    "Auto-teardown target. Defaults to created_at + 7 days. The "
                    "preview-GC workflow tears down anything past this "
                    "timestamp; operators can push it out via the "
                    "extendPreviewTtl mutation in 1/7/30-day increments, "
                    "capped at +30 days from now."
                ),
            ),
        ),
        migrations.RunPython(_backfill_ttl, _noop),
    ]
