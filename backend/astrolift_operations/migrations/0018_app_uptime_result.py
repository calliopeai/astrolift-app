import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0017_alter_applogexport_id_alter_auditexport_id_and_more"),
        ("astrolift_registry", "0030_alter_registeredapp_provisioning_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AppUptimeResult",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("checked_at", models.DateTimeField(db_index=True)),
                ("target_url", models.CharField(max_length=512)),
                ("status_code", models.PositiveIntegerField(blank=True, null=True)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("is_up", models.BooleanField()),
                ("detail", models.CharField(blank=True, default="", max_length=255)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Creator",
                    ),
                ),
                (
                    "deleted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "registered_app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="uptime_results",
                        to="astrolift_registry.registeredapp",
                    ),
                ),
            ],
            options={"ordering": ["-checked_at"]},
        ),
        migrations.AddIndex(
            model_name="appuptimeresult",
            index=models.Index(
                fields=["registered_app", "-checked_at"],
                name="app_uptime_app_checked_idx",
            ),
        ),
    ]
