from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0008_workflowinstance_temporal_run_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalworkflowdefinition",
            name="source_path",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Repository-relative workflow manifest path.",
                max_length=512,
            ),
        ),
        migrations.AddField(
            model_name="historicalworkflowdefinition",
            name="source_ref",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Last repository ref reconciled into this definition.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="historicalworkflowdefinition",
            name="source_repo",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Source repository that declaratively owns this definition.",
                max_length=512,
            ),
        ),
        migrations.AddField(
            model_name="workflowdefinition",
            name="source_path",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Repository-relative workflow manifest path.",
                max_length=512,
            ),
        ),
        migrations.AddField(
            model_name="workflowdefinition",
            name="source_ref",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Last repository ref reconciled into this definition.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="workflowdefinition",
            name="source_repo",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Source repository that declaratively owns this definition.",
                max_length=512,
            ),
        ),
        migrations.AddField(
            model_name="historicalworkflowstage",
            name="agent_ref",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Organization-local agent workload slug used by the workflow manifest.",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="historicalworkflowstage",
            name="environment_spec_slug",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Default org-scoped AgentEnvironmentSpec slug for this stage.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="historicalworkflowstage",
            name="output_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Name under which this stage's structured result is exposed to later stages.",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="workflowstage",
            name="agent_ref",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Organization-local agent workload slug used by the workflow manifest.",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="workflowstage",
            name="environment_spec_slug",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Default org-scoped AgentEnvironmentSpec slug for this stage.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="workflowstage",
            name="output_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Name under which this stage's structured result is exposed to later stages.",
                max_length=100,
            ),
        ),
        migrations.AlterField(
            model_name="historicalworkflowdefinition",
            name="model_label",
            field=models.CharField(
                db_index=True,
                help_text=(
                    'Legacy state-machine target (for example "forms.FormSubmission"). '
                    "Agent pipelines leave this blank; it is not an LLM model selector."
                ),
                max_length=100,
            ),
        ),
        migrations.AlterField(
            model_name="workflowdefinition",
            name="model_label",
            field=models.CharField(
                db_index=True,
                help_text=(
                    'Legacy state-machine target (for example "forms.FormSubmission"). '
                    "Agent pipelines leave this blank; it is not an LLM model selector."
                ),
                max_length=100,
            ),
        ),
        migrations.AlterField(
            model_name="historicalworkflowstage",
            name="prompt",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Agent stage instruction overlay or human-gate approval prompt.",
            ),
        ),
        migrations.AlterField(
            model_name="workflowstage",
            name="prompt",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Agent stage instruction overlay or human-gate approval prompt.",
            ),
        ),
        migrations.AddConstraint(
            model_name="workflowdefinition",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(source_repo=""),
                fields=("organization", "source_repo", "source_path"),
                name="workflowdefinition_org_source_unique",
            ),
        ),
    ]
