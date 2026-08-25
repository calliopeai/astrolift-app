"""A preview environment learns what it is a preview of (#1578 feature 2).

Additive and nullable, so no backfill and no existing row changes meaning:
every environment that exists today is either not a preview (correctly
null) or a preview created before the link existed (null, and left that
way).

Deliberately no data migration guessing lineage for existing previews.
The resolver picks a primary by cluster and name, and applying that
retroactively would stamp a guess as a declared fact on rows nobody
checked -- while previews are ephemeral by construction, so the ones alive
today are gone within their TTL and the next ones get a real answer.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0033_domainsessionhandoff"),
    ]

    operations = [
        migrations.AddField(
            model_name="appenvironment",
            name="previewed_environment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="previews",
                to="astrolift_lifecycle.appenvironment",
                help_text=(
                    "For a preview environment, the environment it is a "
                    "preview OF (#1578 feature 2). Null on every non-preview "
                    "environment."
                ),
            ),
        ),
    ]
