# Hand-written for #677 + #678 — operator-facing sidecar metadata for
# app-level secret literals (expiry, source/provenance).  App secrets
# live in the staged manifest text — this table adds metadata the
# manifest can't carry.

import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0013_registeredapp_secret_approval_policy"),
        ("astrolift_services", "0005_secret_change_proposal"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSecretMetadata",
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
                ("environment_name", models.CharField(blank=True, default="", max_length=64)),
                ("key", models.CharField(max_length=255)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("web", "Web"),
                            ("cli", "Cli"),
                            ("env_paste", "Env Paste"),
                            ("bundle", "Bundle"),
                            ("managed_service", "Managed Service"),
                        ],
                        default="web",
                        max_length=24,
                    ),
                ),
                ("set_at", models.DateTimeField(blank=True, null=True)),
                (
                    "registered_app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="secret_metadata",
                        to="astrolift_registry.registeredapp",
                    ),
                ),
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
                    "deleted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("registered_app", "environment_name", "key"),
                        name="app_secret_metadata_unique_active",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["registered_app", "key"],
                        name="app_secret_meta_app_key_idx",
                    ),
                ],
            },
        ),
    ]
