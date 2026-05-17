# Generated for #419 — approval decision-context bundle:
#   * commit_message + commit_author for the commit-preview card
#   * aborted_reason for the required-rejection-reason flow
#
# Optional / default-blank so existing rows backfill to empty strings
# without a data migration.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0010_scheduledjobrun_trigger_kind_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="commit_message",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="deployment",
            name="commit_author",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="deployment",
            name="aborted_reason",
            field=models.TextField(blank=True, default=""),
        ),
    ]
