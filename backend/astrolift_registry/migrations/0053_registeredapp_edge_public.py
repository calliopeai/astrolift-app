from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_registry", "0052_brain_typed_token_rollout")]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="edge_public",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
