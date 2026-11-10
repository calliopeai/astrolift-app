from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_installidentity"),
    ]

    operations = [
        # Retiring the integration must not erase existing profile/history metadata.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name="historicalprofile", name="rocket_id"),
                migrations.RemoveField(model_name="profile", name="rocket_id"),
            ],
        ),
    ]
