"""Store the install's generated id (#479, #2161).

No data step: ``core.install_identity.current_install_id`` creates the row
the first time it runs without ``ASTROLIFT_INSTALL_ID``, and a concurrent
first call from another replica reads the winner's row.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_mutationauditlog_org_time_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstallIdentity",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False),
                ),
                ("install_id", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("id", 1)), name="install_identity_single_row")
                ],
            },
        ),
    ]
