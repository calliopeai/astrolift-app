from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0007_org_slug_unique_live_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalworkflowinstance",
            name="temporal_run_id",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="workflowinstance",
            name="temporal_run_id",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
