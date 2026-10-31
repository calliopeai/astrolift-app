from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_clusters", "0029_clear_stale_domain_failures")]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="allow_public_apps",
            field=models.BooleanField(default=True, db_default=True),
        ),
    ]
