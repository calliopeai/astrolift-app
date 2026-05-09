from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_registry', '0002_registeredapp_preview_screenshot_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='registeredapp',
            name='manifest_raw_staged',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='registeredapp',
            name='last_synced_hash',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
    ]
