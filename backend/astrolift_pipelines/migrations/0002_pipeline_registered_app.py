# Migration: add Pipeline.registered_app FK to RegisteredApp (#96).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_pipelines', '0001_initial'),
        ('astrolift_registry', '0025_workload_agent_variant_runtime'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipeline',
            name='registered_app',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pipelines',
                to='astrolift_registry.registeredapp',
            ),
        ),
    ]
