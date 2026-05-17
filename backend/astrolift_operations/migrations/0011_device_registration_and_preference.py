# Generated for #476 + #499 — push device registry + per-user notification
# preference catalog.

import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0001_initial"),
        ("astrolift_identity", "0014_merge_session_branches"),
        ("astrolift_operations", "0010_app_log_export"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceRegistration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                (
                    "device_token",
                    models.CharField(db_index=True, max_length=512),
                ),
                (
                    "platform",
                    models.CharField(
                        choices=[
                            ("ios", "iOS"),
                            ("android", "Android"),
                            ("web_push", "Web push"),
                        ],
                        max_length=16,
                    ),
                ),
                ("label", models.CharField(blank=True, default="", max_length=200)),
                ("driver", models.CharField(max_length=32)),
                ("registered_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("stale_at", models.DateTimeField(blank=True, null=True)),
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
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="astrolift_device_registrations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "enrolled_session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="enrolled_devices",
                        to="astrolift_identity.astroliftsession",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="device_registrations",
                        to="astrolift_identity.organization",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "platform"], name="device_reg_user_plat_idx"),
                    models.Index(fields=["organization", "user"], name="device_reg_org_user_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("user", "device_token"),
                        name="device_reg_user_token_live_uniq",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("push", "Mobile push"),
                            ("email", "Email"),
                            ("webhook", "Webhook"),
                        ],
                        max_length=16,
                    ),
                ),
                ("event_kind", models.CharField(max_length=64)),
                ("enabled", models.BooleanField()),
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
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "channel"], name="notif_pref_user_chan_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("user", "channel", "event_kind"),
                        name="notif_pref_user_chan_kind_live_uniq",
                    ),
                ],
            },
        ),
    ]
