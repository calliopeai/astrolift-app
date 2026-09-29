from django.db import migrations, models


class Migration(migrations.Migration):
    """Who opened a preview and why its build failed (#2155)."""

    dependencies = [
        ("astrolift_lifecycle", "0042_appenvironment_k8s_namespace_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="previewenvironment",
            name="opened_by_login",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="previewenvironment",
            name="failure_reason",
            field=models.TextField(blank=True, default=""),
        ),
    ]
