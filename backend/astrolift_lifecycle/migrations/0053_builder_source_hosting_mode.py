"""Builder source intents for ordinary project apps without a brain (#2471).

Existing rows and writers from before this change keep ``brain`` intents
through the DB default; the check constraint keeps a brain on every brain
intent and none on a project intent.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_lifecycle", "0052_reprovision_reachability")]

    operations = [
        migrations.AddField(
            model_name="builderbrainbuild",
            name="hosting_mode",
            field=models.CharField(
                choices=[("brain", "Brain"), ("project", "Project")],
                db_default="brain",
                default="brain",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="builderbrainbuild",
            name="brain",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="source_builds",
                to="astrolift_registry.registeredapp",
            ),
        ),
        migrations.AddConstraint(
            model_name="builderbrainbuild",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(hosting_mode="brain", brain__isnull=False)
                    | models.Q(hosting_mode="project", brain__isnull=True)
                ),
                name="builder_source_hosting_mode_brain",
            ),
        ),
    ]
