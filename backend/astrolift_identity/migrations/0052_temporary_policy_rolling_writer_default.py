from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_identity", "0051_temporary_app_policy")]

    operations = [
        migrations.AlterField(
            model_name="organization",
            name="temporary_app_policy",
            field=models.JSONField(default=dict, blank=True, db_default={}),
        ),
    ]
