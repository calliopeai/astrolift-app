"""Add ``RegisteredApp.security_policy`` (#313).

Sparse JSON blob persisting the supply-chain / scanner policy the
PromoteDeploymentWorkflow consults at deploy-gate time. Defaults to
``{}`` so the resolved view (via the model property) keeps falling
back to platform defaults until the operator sets a knob explicitly.

Additive — no data backfill needed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0009_registeredapp_project_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="security_policy",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
