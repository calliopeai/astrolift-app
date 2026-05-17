# Hand-written for #441 — persist the bundle's projected key names so
# operator UI can show keyCount without an on-read round-trip to the
# platform secrets backend (Vault / SecretsManager / GSM / KeyVault)
# per attachment.  Refreshed by the rotation activity + lazy on-read.

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0003_managedservice_last_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="secretbundle",
            name="last_known_keys",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=255),
                blank=True,
                default=list,
                size=None,
            ),
        ),
        migrations.AddField(
            model_name="secretbundle",
            name="last_key_enum_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
