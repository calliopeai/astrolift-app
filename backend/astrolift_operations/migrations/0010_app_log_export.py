# Generated for #483 — app log export / vendor handoff bundle.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0001_initial"),
        ("astrolift_operations", "0009_alertmute"),
        ("astrolift_registry", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AppLogExport",
            fields=[
                (
                    "id",
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("guid", core.fields.uuid_v7.UUIDv7Field(db_index=True, unique=True)),
                ("environment_name", models.CharField(blank=True, default="", max_length=128)),
                ("pod_name", models.CharField(blank=True, default="", max_length=255)),
                ("workload_name", models.CharField(blank=True, default="", max_length=255)),
                ("container", models.CharField(blank=True, default="", max_length=255)),
                (
                    "format",
                    models.CharField(
                        choices=[
                            ("csv", "CSV"),
                            ("ndjson", "Newline-delimited JSON"),
                            ("txt", "Raw text"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ready", "Ready"),
                            ("expired", "Expired"),
                            ("failed", "Failed"),
                        ],
                        default="ready",
                        max_length=16,
                    ),
                ),
                ("row_count", models.IntegerField(default=0)),
                ("byte_count", models.BigIntegerField(default=0)),
                ("sha256", models.CharField(blank=True, default="", max_length=64)),
                ("relative_path", models.CharField(max_length=512)),
                ("token_hash", models.CharField(db_index=True, max_length=64)),
                ("filters_snapshot", models.JSONField(blank=True, default=dict)),
                ("error_message", models.CharField(blank=True, default="", max_length=512)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="app_log_exports",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "registered_app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="log_exports",
                        to="astrolift_registry.registeredapp",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="app_log_exports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["organization", "-created_at"],
                        name="app_log_export_org_time_idx",
                    ),
                    models.Index(
                        fields=["registered_app", "-created_at"],
                        name="app_log_export_app_time_idx",
                    ),
                ],
            },
        ),
    ]
