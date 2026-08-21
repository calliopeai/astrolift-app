"""JobRun keeps the tail of its pod's output (#1218).

`astro pipeline logs` was stuck as not-implemented because no pipeline run
captured logs anywhere: `StepRun.log_excerpt` did not exist, and the module
that claimed to write it (`log_streaming.py`) had no importers and imported
two things that were never written.

The column is on JobRun rather than StepRun because a job is one container.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_pipelines", "0004_remove_runner_runner_org_status_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobrun",
            name="log_excerpt",
            field=models.TextField(blank=True, default=""),
        ),
    ]
