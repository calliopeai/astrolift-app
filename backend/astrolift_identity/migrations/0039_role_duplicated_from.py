import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0038_ui_preferences_restricted_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="duplicated_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="duplicates",
                to="astrolift_identity.role",
            ),
        ),
    ]
