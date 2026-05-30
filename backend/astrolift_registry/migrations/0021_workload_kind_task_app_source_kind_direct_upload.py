"""Add task to Workload.Kind and direct_upload to RegisteredApp.SourceKind.

Workload.Kind gains TASK = "task" — the one-shot batch/v1 Job primitive
added alongside the manifest parser / renderer in the runtime-primitives
effort (#792, #793).

RegisteredApp.SourceKind gains DIRECT_UPLOAD = "direct_upload" — apps
promoted from a Calliope App Builder DevEnvironment (#767, #768). This
choice existed in the model since that PR but the migration was never
generated; Django's state diverged silently because there is no
makemigrations --check gate in CI.

Both are additive: no data migration needed, no column type change,
no constraint change. Existing rows remain valid.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_registry", "0020_workload_volumes"),
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
                ],
                default="deployment",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="registeredapp",
            name="source_kind",
            field=models.CharField(
                choices=[
                    ("github", "Github"),
                    ("gitlab", "Gitlab"),
                    ("bitbucket", "Bitbucket"),
                    ("gitea", "Gitea"),
                    ("git_url", "Git Url"),
                    ("direct_upload", "Direct Upload"),
                ],
                default="github",
                max_length=32,
            ),
        ),
    ]
