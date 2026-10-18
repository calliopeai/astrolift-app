from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_registry", "0049_expiry_cleanup")]

    operations = [
        migrations.AlterField(
            model_name="registeredapp",
            name="delete_data_on_expiry",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.AlterField(
            model_name="registeredapp",
            name="expiry_warnings_sent",
            field=models.JSONField(default=list, blank=True, db_default=[]),
        ),
        migrations.AlterField(
            model_name="registeredapp",
            name="expiry_cleanup",
            field=models.JSONField(default=list, blank=True, editable=False, db_default=[]),
        ),
    ]
