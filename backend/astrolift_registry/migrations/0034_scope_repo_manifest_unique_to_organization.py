from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0033_registeredapp_ci_secrets_pushed_at"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="registeredapp",
            name="registered_app_repo_manifest_unique",
        ),
        migrations.AddConstraint(
            model_name="registeredapp",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(source_repo=""),
                fields=("organization", "source_repo", "manifest_path"),
                name="registered_app_repo_manifest_unique",
            ),
        ),
    ]
