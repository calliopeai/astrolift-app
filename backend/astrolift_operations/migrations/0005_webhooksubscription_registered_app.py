from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_operations', '0004_webhooksubscription_disabled_at_and_more'),
        ('astrolift_registry', '0003_registeredapp_manifest_staging_and_anchor'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhooksubscription',
            name='registered_app',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='webhook_subscriptions',
                to='astrolift_registry.registeredapp',
            ),
        ),
    ]
