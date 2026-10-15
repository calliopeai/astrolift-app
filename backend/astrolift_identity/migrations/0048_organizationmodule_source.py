from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0047_step_up_pin"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationmodule",
            name="source",
            field=models.CharField(
                choices=[("org_admin", "Org Admin"), ("licence", "Licence")],
                db_default="org_admin",
                max_length=16,
            ),
        ),
    ]
