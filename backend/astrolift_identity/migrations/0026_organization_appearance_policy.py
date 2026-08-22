from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0025_organization_pipeline_definition_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="appearance_default",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="organization",
            name="appearance_locked",
            field=models.BooleanField(default=False),
        ),
    ]
