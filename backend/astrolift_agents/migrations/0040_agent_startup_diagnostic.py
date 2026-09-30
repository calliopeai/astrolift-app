from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_agents", "0039_backfill_agent_spec_and_box_owner")]

    operations = [
        migrations.AddField(
            model_name=model,
            name="startup_diagnostic",
            field=models.JSONField(blank=True, default=dict),
        )
        for model in ("agenttask", "agentbox")
    ]
