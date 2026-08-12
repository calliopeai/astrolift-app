from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0012_managedservice_kind_faas"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="secretbundle",
            constraint=models.UniqueConstraint(
                fields=("organization", "slug"),
                condition=models.Q(team__isnull=True, deleted_at__isnull=True),
                name="secret_bundle_slug_unique_active_per_org",
            ),
        ),
    ]
