# Hand-written for #756 — per-message SES email event log.
#
# Append-only row created by the SNS webhook receiver every time SES
# delivers a SEND / DELIVERY / BOUNCE / COMPLAINT / OPEN / CLICK
# notification. The managed_service FK is nullable so an out-of-band
# delivery (e.g. a notification that beats the ManagedService row into
# existence) is recorded rather than dropped.

import django.db.models.deletion
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0007_appsecretmetadata_scope"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailEvent",
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
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("message_id", models.CharField(db_index=True, max_length=255)),
                ("recipient", models.CharField(db_index=True, max_length=320)),
                ("subject", models.CharField(blank=True, default="", max_length=998)),
                (
                    "event_kind",
                    models.CharField(
                        choices=[
                            ("send", "Send"),
                            ("delivery", "Delivery"),
                            ("bounce", "Bounce"),
                            ("complaint", "Complaint"),
                            ("open", "Open"),
                            ("click", "Click"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "managed_service",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_events",
                        to="astrolift_services.managedservice",
                    ),
                ),
            ],
            options={
                "ordering": ["-occurred_at"],
                "indexes": [
                    models.Index(
                        fields=["managed_service", "event_kind", "occurred_at"],
                        name="emailevent_svc_kind_time_idx",
                    ),
                    models.Index(
                        fields=["message_id", "event_kind"],
                        name="emailevent_msg_kind_idx",
                    ),
                ],
            },
        ),
    ]
