# Hand-written for #752 — secret environment scope field.
# Adds 'scope' to AppSecretMetadata so operators can restrict a secret
# to a specific audience (all / production / preview / preview:<branch>).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0006_app_secret_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsecretmetadata",
            name="scope",
            field=models.CharField(
                default="all",
                max_length=48,
                help_text=(
                    "Audience scope for this secret row. 'all' = every environment "
                    "(default); 'production' = non-preview envs only; 'preview' = "
                    "any active preview env; 'preview:<branch>' = one specific "
                    "preview branch."
                ),
            ),
        ),
    ]
