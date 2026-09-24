from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_agents", "0031_agentenvironmentspec_box_workspace")]
    operations = [
        migrations.AddField(
            model_name="agentenvironmentspec",
            name="model_gateway",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="agenttask",
            name="model_gateway_agent_id",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="agentbox",
            name="model_gateway_agent_id",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="agentbox",
            name="model_gateway_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
