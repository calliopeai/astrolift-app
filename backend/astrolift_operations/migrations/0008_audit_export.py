# Generated for #433 — audit compliance bundle.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0001_initial"),
        ("astrolift_operations", "0007_webhook_rotation_format_and_delivery"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditExport",
            fields=[
                (
                    "id",
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("guid", core.fields.uuid_v7.UUIDv7Field(db_index=True, unique=True)),
                (
                    "format",
                    models.CharField(
                        choices=[
                            ("csv", "CSV"),
                            ("ndjson", "Newline-delimited JSON"),
                        ],
                        max_length=16,
                    ),
                ),
                ("row_count", models.IntegerField(default=0)),
                ("byte_count", models.BigIntegerField(default=0)),
                ("sha256", models.CharField(blank=True, default="", max_length=64)),
                ("relative_path", models.CharField(max_length=512)),
                ("token_hash", models.CharField(db_index=True, max_length=64)),
                ("filters_snapshot", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="audit_exports",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_exports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["organization", "-created_at"],
                        name="audit_export_org_time_idx",
                    ),
                ],
            },
        ),
    ]
