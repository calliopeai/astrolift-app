"""Add the AgentSkillRef join model (spec 38, Phase 2).

The definitional counterpart to ``BriefSkillRef``: records the Skills an
``agent`` Workload carries at the definition level (set at registration in
Phase 3), which the assembly service reads to build the agent's Brief.

Purely additive ``CreateModel``; no data migration. The cross-app FK
``AgentSkillRef.workload -> astrolift_registry.workload`` makes this
migration depend on ``astrolift_registry.0028_workload_run_spec`` (the
established pattern — see ``0001_initial`` / ``WorkloadToolDef``).
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_agents', '0011_workflowwebhook_agent_definition_and_more'),
        ('astrolift_registry', '0028_workload_run_spec'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentSkillRef',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('position', models.PositiveIntegerField(default=0)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('skill', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='agent_refs', to='astrolift_agents.skill')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('workload', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_skill_refs', to='astrolift_registry.workload')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('workload', 'skill'), name='agentskillref_unique_per_workload')],
            },
        ),
    ]
