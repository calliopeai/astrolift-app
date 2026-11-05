from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_registry", "0053_registeredapp_edge_public")]

    operations = [
        migrations.AddField(
            model_name="braintokenkey",
            name="standby",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
