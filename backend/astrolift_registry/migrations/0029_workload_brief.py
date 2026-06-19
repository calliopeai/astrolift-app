"""Add the definitional ``brief`` FK to Workload (spec 38, Phase 2).

An ``agent`` Workload links to the assembled Brief it fetches at boot
(set at registration in Phase 3). ``on_delete=SET_NULL`` so revoking or
deleting a Brief leaves the workload registered (it can be re-assembled).

Additive ``AddField`` with ``null=True``; a no-op for every existing row.
The cross-app FK ``Workload.brief -> astrolift_agents.brief`` makes this
migration depend on ``astrolift_agents.0012_agentskillref`` (the current
agents leaf), keeping a single linear leaf per app and a clean graph.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_agents', '0012_agentskillref'),
        ('astrolift_registry', '0028_workload_run_spec'),
    ]

    operations = [
        migrations.AddField(
            model_name='workload',
            name='brief',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='agent_workloads', to='astrolift_agents.brief'),
        ),
    ]
