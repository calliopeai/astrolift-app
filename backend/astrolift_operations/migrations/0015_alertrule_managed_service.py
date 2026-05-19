"""Bind AlertRule to a specific ManagedService instance (#757).

Adds an optional FK so per-service predicate kinds (ses_bounce_rate,
ses_complaint_rate, …) can resolve the live driver for evaluation.
Nullable + CASCADE — existing rules keep working with managed_service
= NULL; deleting a service drops its bound rules with it (no orphans).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0014_user_alert_subscription"),
        ("astrolift_services", "0007_appsecretmetadata_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="alertrule",
            name="managed_service",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Optional binding to a specific ManagedService instance. "
                    "Required for per-service predicate kinds (e.g. "
                    "ses_bounce_rate, ses_complaint_rate) so the evaluator "
                    "knows which driver instance to pull live metrics from. "
                    "Cascades on service delete — when an operator removes the "
                    "service, its rules go with it."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="alert_rules",
                to="astrolift_services.managedservice",
            ),
        ),
    ]
