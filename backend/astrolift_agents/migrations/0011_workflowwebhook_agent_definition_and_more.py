"""Agent trigger binding on WorkflowWebhook (spec 33, PR-6).

Lets a webhook bind to an agent ``Workload(kind=agent)`` so a firing
dispatches an AgentTask through the PR-1 dispatch path instead of
launching a WorkflowInstance.

  agent_definition    — new nullable FK to astrolift_registry.Workload.
  workflow_definition — widened to nullable (was required) so a row can
                        target an agent instead. Widening only — existing
                        rows keep their value, and the XOR check below
                        holds for them (definition set, agent null).
  input_mapping       — help-text only; the column is reused to shape the
                        agent dispatch payload (apply_input_mapping).

Plus a DB CheckConstraint enforcing the workflow-vs-agent XOR (exactly
one target per row), and two supporting indexes:
  agent_task_agentdef_status_idx  — cheap per-agent in-flight count for
                                    the PR-6 Loop controller.
  wfwebhook_agent_enabled_idx     — cheap "agent webhooks bound here".

Purely additive (one AddField, two AlterFields that widen/annotate, two
AddIndex, one AddConstraint); no data migration. The Loop run mode needs
NO column — it reuses ``Workload.run_max_parallel`` from PR-1.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_agents', '0010_agentenvironmentspec_config_manifest_path'),
        ('astrolift_identity', '0018_resync_system_roles_agent_task_watch'),
        ('astrolift_registry', '0028_workload_run_spec'),
        ('workflows', '0003_historicalworkflowdefinition_pattern_kind_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowwebhook',
            name='agent_definition',
            field=models.ForeignKey(blank=True, help_text='Agent Workload this webhook dispatches a Task for when it fires (spec 33, PR-6). Mutually exclusive with workflow_definition.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='agent_webhooks', to='astrolift_registry.workload'),
        ),
        migrations.AlterField(
            model_name='workflowwebhook',
            name='input_mapping',
            field=models.JSONField(blank=True, default=dict, help_text="Key-mapping spec applied to the incoming payload. For a workflow_definition target it maps payload fields to the workflow's input schema; for an agent_definition target (PR-6) it shapes the dispatch payload handed to the agent Task (see services.workflow_triggers.apply_input_mapping)."),
        ),
        migrations.AlterField(
            model_name='workflowwebhook',
            name='workflow_definition',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='webhooks', to='workflows.workflowdefinition'),
        ),
        migrations.AddIndex(
            model_name='agenttask',
            index=models.Index(fields=['agent_definition', 'status'], name='agent_task_agentdef_status_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowwebhook',
            index=models.Index(fields=['agent_definition', 'enabled'], name='wfwebhook_agent_enabled_idx'),
        ),
        migrations.AddConstraint(
            model_name='workflowwebhook',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('agent_definition__isnull', True), ('workflow_definition__isnull', False)), models.Q(('agent_definition__isnull', False), ('workflow_definition__isnull', True)), _connector='OR'), name='wfwebhook_exactly_one_target'),
        ),
    ]
