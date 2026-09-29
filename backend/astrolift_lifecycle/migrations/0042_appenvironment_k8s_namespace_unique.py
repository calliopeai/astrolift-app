from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0041_backfill_preview_environment_namespace"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="appenvironment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), models.Q(("k8s_namespace", ""), _negated=True)),
                fields=("k8s_namespace",),
                name="appenv_k8s_namespace_unique_active",
            ),
        ),
    ]
