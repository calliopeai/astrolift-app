from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_registry", "0054_braintokenkey_standby")]

    # Reversing this must not drop the column. A rollback of an app this one
    # depends on (agents) reverses it on the way down, and a dropped column
    # would be erased by a rollback that may then be refused. The column is
    # kept on reverse; the state change alone is reversed.
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="registeredapp",
                    name="security_headers",
                    field=models.BooleanField(default=False, db_default=False),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE astrolift_registry_registeredapp "
                        "ADD COLUMN IF NOT EXISTS security_headers boolean NOT NULL DEFAULT false"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
