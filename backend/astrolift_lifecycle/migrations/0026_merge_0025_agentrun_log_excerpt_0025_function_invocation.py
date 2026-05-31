"""Merge migration — resolves the 0025 conflict between agentrun_log_excerpt
and function_invocation, both of which depend on 0024_task_run_agent_run."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_lifecycle", "0025_agentrun_log_excerpt"),
        ("astrolift_lifecycle", "0025_function_invocation"),
    ]

    operations = []
