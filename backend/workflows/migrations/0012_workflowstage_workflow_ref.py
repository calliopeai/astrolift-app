from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0011_repair_workflow_definition_projects"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalworkflowstage",
            name="workflow_ref",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Visible child WorkflowDefinition slug used by kind=workflow stages.",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="workflowstage",
            name="workflow_ref",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Visible child WorkflowDefinition slug used by kind=workflow stages.",
                max_length=100,
            ),
        ),
        migrations.AlterField(
            model_name="historicalworkflowstage",
            name="kind",
            field=models.CharField(
                choices=[
                    ("agent_dispatch", "Agent Dispatch"),
                    ("human_gate", "Human Gate"),
                    ("checkpoint", "Checkpoint"),
                    ("aggregation", "Aggregation"),
                    ("workflow", "Workflow"),
                ],
                default="agent_dispatch",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="workflowstage",
            name="kind",
            field=models.CharField(
                choices=[
                    ("agent_dispatch", "Agent Dispatch"),
                    ("human_gate", "Human Gate"),
                    ("checkpoint", "Checkpoint"),
                    ("aggregation", "Aggregation"),
                    ("workflow", "Workflow"),
                ],
                default="agent_dispatch",
                max_length=32,
            ),
        ),
    ]
