from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_agents", "0029_agent_input_request_id")]
    operations = [
        migrations.AddField(
            model_name="agentenvironmentspec",
            name="run_as_non_root",
            field=models.BooleanField(default=False),
        ),
    ]
