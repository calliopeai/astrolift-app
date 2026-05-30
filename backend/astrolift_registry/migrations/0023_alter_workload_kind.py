"""Add workflow to Workload.Kind choices.

The ``workflow`` Kind (Temporal worker deployment, #796) was added to
the ``Workload.Kind`` enum in the same runtime-primitives push as
``agent`` (#795) but its choices migration was never generated — the
agent migration (0022) only carried ``agent``. Without this the model
state diverges from migrations and ``makemigrations --check`` fails on
fresh test DBs.

Choices-only change: ``kind`` is a plain ``CharField`` so choices are
not enforced at the DB level — no DDL, no data migration, every existing
row stays valid.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0022_workload_kind_agent_dispatch_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workload",
            name="kind",
            field=models.CharField(
                choices=[
                    ("deployment", "Deployment"),
                    ("statefulset", "Statefulset"),
                    ("job", "Job"),
                    ("cronjob", "Cronjob"),
                    ("task", "Task"),
                    ("agent", "Agent"),
                    ("workflow", "Workflow"),
                ],
                default="deployment",
                max_length=32,
            ),
        ),
    ]
