"""DeviceFlowSession — mobile QR enrollment fields (#494).

Adds the optional ``alft_enroll_…`` token hash + last4 + expiry, the
``enrollment_consumed_at`` terminal stamp, a label captured at QR-
generation time, the ``origin`` discriminator (``device_flow`` vs
``enrollment``), and a new ``pre_approved`` lifecycle state. The
existing browser-approved CLI flow keeps its ``state``/``origin``
defaults so the migration is back-compat — rows minted before this
ship land in ``origin=device_flow`` automatically.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0012_deviceflowsession"),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceflowsession",
            name="enrollment_token_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="deviceflowsession",
            name="enrollment_token_last_4",
            field=models.CharField(blank=True, default="", max_length=4),
        ),
        migrations.AddField(
            model_name="deviceflowsession",
            name="enrollment_token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deviceflowsession",
            name="enrollment_consumed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deviceflowsession",
            name="enrollment_label",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="deviceflowsession",
            name="origin",
            field=models.CharField(
                choices=[
                    ("device_flow", "device_flow"),
                    ("enrollment", "enrollment"),
                ],
                db_index=True,
                default="device_flow",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="deviceflowsession",
            name="state",
            field=models.CharField(
                choices=[
                    ("pending", "pending"),
                    ("approved", "approved"),
                    ("pre_approved", "pre_approved"),
                    ("denied", "denied"),
                    ("consumed", "consumed"),
                    ("expired", "expired"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="deviceflowsession",
            index=models.Index(
                fields=["approved_user", "origin", "state"],
                name="dfs_user_origin_state_idx",
            ),
        ),
    ]
