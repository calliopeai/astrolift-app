# Generated for #426 — webhooks operator UX bundle.

import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_operations", "0006_alertrule_alertevent_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="webhooksubscription",
            name="secret_hash_previous",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="webhooksubscription",
            name="secret_rotated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="webhooksubscription",
            name="format",
            field=models.CharField(
                choices=[
                    ("generic", "Generic (Astrolift envelope)"),
                    ("slack", "Slack incoming webhook"),
                    ("discord", "Discord webhook"),
                ],
                default="generic",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="WebhookDelivery",
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
                ("event_type", models.CharField(db_index=True, max_length=128)),
                ("retry_attempt", models.PositiveSmallIntegerField(default=1)),
                ("status_code", models.IntegerField(blank=True, null=True)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("success", models.BooleanField(default=False)),
                ("is_test", models.BooleanField(default=False)),
                ("request_payload_excerpt", models.TextField(blank=True, default="")),
                ("response_body_excerpt", models.TextField(blank=True, default="")),
                ("error", models.CharField(blank=True, default="", max_length=512)),
                ("delivery_id", models.CharField(blank=True, default="", max_length=64)),
                ("delivered_at", models.DateTimeField(db_index=True)),
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
                    "subscription",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="astrolift_operations.webhooksubscription",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="webhookdelivery",
            index=models.Index(
                fields=["subscription", "-delivered_at"],
                name="wh_delivery_sub_time_idx",
            ),
        ),
    ]
