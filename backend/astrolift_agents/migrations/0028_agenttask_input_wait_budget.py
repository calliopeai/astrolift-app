from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_agents", "0027_agenttask_cancel_requested_at")]
    operations = [
        migrations.AddField(
            model_name="agenttask",
            name="input_wait_budget_seconds",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
