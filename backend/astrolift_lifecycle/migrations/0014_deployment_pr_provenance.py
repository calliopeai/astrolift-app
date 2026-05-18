# Generated for #722 — GitHub PR + author-avatar provenance on Deployment:
#   * pr_number — populated by push webhook when the deploy was triggered
#     from a PR; rendered as "PR #1234" link on the deployments table
#     per #651
#   * commit_author_avatar_url — captured at deploy creation so the FE
#     can render the avatar circle without re-hitting the GitHub commits
#     API on every list-deployments call
#
# Both default-blank/zero so existing rows backfill cleanly without a
# data migration. The FE renders a dash for pr_number=0.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0013_deploytoken_last_used_ip_agent"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="pr_number",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="deployment",
            name="commit_author_avatar_url",
            field=models.URLField(blank=True, default=""),
        ),
    ]
