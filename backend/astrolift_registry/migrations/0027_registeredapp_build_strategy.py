from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_registry", "0026_add_build_mode_to_registered_app"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="build_strategy",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("dockerfile", "Dockerfile"),
                    ("buildpacks", "Buildpacks"),
                    ("nixpacks", "Nixpacks"),
                ],
                default="off",
                max_length=32,
            ),
        ),
    ]
