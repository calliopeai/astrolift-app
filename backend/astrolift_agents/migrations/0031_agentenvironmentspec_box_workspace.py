from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_agents", "0030_agentenvironmentspec_run_as_non_root")]
    operations = [
        migrations.AddField(
            model_name="agentenvironmentspec",
            name="box_workspace",
            field=models.BooleanField(default=False),
        ),
    ]
