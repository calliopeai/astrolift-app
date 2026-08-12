from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0018_agenttask_agent_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="agenttask",
            name="callback_token_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
