"""DeviceFlowSession (#475).

The polling-state row for the CLI / mobile browser device flow.
``session_guid`` is the opaque polling identifier returned to the
client on /start; ``refresh_token_hash`` stores the SHA-256 of the
most recent rotating refresh secret.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_identity", "0011_merge_20260517_1933"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceFlowSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
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
                ("session_guid", models.CharField(db_index=True, max_length=64, unique=True)),
                ("client_label", models.CharField(blank=True, default="", max_length=128)),
                ("client_kind", models.CharField(blank=True, default="cli", max_length=32)),
                ("user_agent", models.CharField(blank=True, default="", max_length=512)),
                ("client_ip", models.GenericIPAddressField(blank=True, null=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "pending"),
                            ("approved", "approved"),
                            ("denied", "denied"),
                            ("consumed", "consumed"),
                            ("expired", "expired"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("denied_at", models.DateTimeField(blank=True, null=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("polled_at", models.DateTimeField(blank=True, null=True)),
                ("refresh_token_hash", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("refresh_token_last_4", models.CharField(blank=True, default="", max_length=4)),
                ("refresh_token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("access_token_expires_at", models.DateTimeField(blank=True, null=True)),
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
                (
                    "approved_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_device_flows",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="device_flow_sessions",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "api_token",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="device_flow_sessions",
                        to="astrolift_identity.apitoken",
                    ),
                ),
            ],
            options={
                "verbose_name": "Device-flow session",
                "verbose_name_plural": "Device-flow sessions",
                "abstract": False,
            },
        ),
        migrations.AddIndex(
            model_name="deviceflowsession",
            index=models.Index(fields=["state", "expires_at"], name="dfs_state_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="deviceflowsession",
            index=models.Index(fields=["approved_user", "state"], name="dfs_user_state_idx"),
        ),
    ]
