from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_identity', '0005_organization_allow_user_profile_edit'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='metrics_retention_days_default',
            field=models.PositiveIntegerField(default=90),
        ),
        migrations.AddField(
            model_name='organization',
            name='metrics_rollup_retention_days_default',
            field=models.PositiveIntegerField(default=365),
        ),
        migrations.AddField(
            model_name='organization',
            name='trace_retention_days_default',
            field=models.PositiveIntegerField(default=14),
        ),
    ]
