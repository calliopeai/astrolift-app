# Generated for issue #452 — first-run onboarding wizard state.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_identity', '0009_apitoken_last_used_ip_agent'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='onboarding_completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
