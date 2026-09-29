"""Record where a skill was imported from (#2155).

Nothing on an existing row says whether it was imported, so every row
starts as not imported; the next import or agent re-sync stamps it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0036_agenttask_initiator_trigger"),
    ]

    operations = [
        migrations.AddField(
            model_name="skill",
            name="source_kind",
            field=models.CharField(
                blank=True,
                choices=[
                    ("repo_import", "Repo Import"),
                    ("agent_repo", "Agent Repo"),
                    ("org_repo", "Org Repo"),
                    ("catalogue", "Catalogue"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="skill",
            name="source_ref",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
