import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0032_model_gateway"),
        ("astrolift_operations", "0025_zentinelle_connection"),
    ]
    operations = [
        migrations.AddField(
            model_name="agenttask",
            name="model_gateway_connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="astrolift_operations.zentinelleconnection",
            ),
        ),
        migrations.AddField(
            model_name="agentbox",
            name="model_gateway_connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="astrolift_operations.zentinelleconnection",
            ),
        ),
        migrations.AddField(
            model_name="agentbox",
            name="model_gateway_lifetime_ends_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
