"""Reconcile ResourceFile.file upload_to from hardcoded 'dev' to settings.GS_DIR.

Migration 0003_alter_resourcefile_file set upload_to='dev'. The model has
always used upload_to=settings.GS_DIR; this migration syncs the recorded
migration state with the model definition to clear the 'have changes not
reflected in a migration' warning on prod where GS_DIR != 'dev'.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_delete_historicalmetabasechart"),
    ]

    operations = [
        migrations.AlterField(
            model_name="resourcefile",
            name="file",
            field=models.FileField(blank=True, null=True, upload_to=settings.GS_DIR),
        ),
    ]
