from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_identity", "0052_temporary_policy_rolling_writer_default")]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="trusted_domain_signup",
            field=models.BooleanField(
                default=False,
                db_default=False,
                help_text="Create an account on first sign-in for a verified email under a trusted domain.",
            ),
        ),
    ]
