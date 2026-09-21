from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_agents", "0028_agenttask_input_wait_budget")]
    operations = [
        migrations.AddField(
            model_name="agenttaskinputmessage",
            name="client_request_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="agenttaskinputmessage",
            constraint=models.UniqueConstraint(
                fields=("agent_task", "client_request_id"), name="agentinput_task_request_unique"
            ),
        ),
    ]
