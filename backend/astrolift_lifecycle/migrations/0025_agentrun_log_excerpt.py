"""
Add AgentRun.log_excerpt — streaming log ring buffer from the Dispatch
Service (#51).

Additive new column; no existing data is affected.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_lifecycle", "0024_task_run_agent_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrun",
            name="log_excerpt",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Streaming log ring buffer (≤10k lines) from the Dispatch Service. "
                    "Full log is in object storage."
                ),
            ),
        ),
    ]
