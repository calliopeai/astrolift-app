import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

SQL = """
LOCK TABLE astrolift_operations_notification IN SHARE ROW EXCLUSIVE MODE;
WITH numbered AS (
    SELECT id, row_number() OVER (PARTITION BY organization_id, user_id ORDER BY id) AS seq
    FROM astrolift_operations_notification
)
UPDATE astrolift_operations_notification n SET inbox_sequence = numbered.seq
FROM numbered WHERE n.id = numbered.id;
INSERT INTO astrolift_operations_notificationinboxcounter (organization_id, user_id, last_sequence)
SELECT organization_id, user_id, max(inbox_sequence)
FROM astrolift_operations_notification GROUP BY organization_id, user_id;

CREATE FUNCTION astrolift_notification_inbox_admit() RETURNS trigger AS $$
BEGIN
    INSERT INTO astrolift_operations_notificationinboxcounter (organization_id, user_id, last_sequence)
    VALUES (NEW.organization_id, NEW.user_id, 1)
    ON CONFLICT (organization_id, user_id) DO UPDATE
    SET last_sequence = astrolift_operations_notificationinboxcounter.last_sequence + 1
    RETURNING last_sequence INTO NEW.inbox_sequence;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER astrolift_notification_inbox_admission
BEFORE INSERT ON astrolift_operations_notification
FOR EACH ROW EXECUTE FUNCTION astrolift_notification_inbox_admit();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0034_app_expiring_kinds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="inbox_sequence",
            field=models.BigIntegerField(default=0, db_default=0, editable=False),
        ),
        migrations.AddField(
            model_name="notification",
            name="expiry_warning",
            field=models.JSONField(null=True, blank=True),
        ),
        migrations.CreateModel(
            name="NotificationInboxCounter",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        primary_key=True, serialize=False, auto_created=True, verbose_name="ID"
                    ),
                ),
                ("last_sequence", models.BigIntegerField(default=0, db_default=0)),
                (
                    "organization",
                    models.ForeignKey(
                        to="astrolift_identity.organization", on_delete=django.db.models.deletion.CASCADE
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "user"), name="notification_inbox_counter_unique"
                    )
                ]
            },
        ),
        migrations.RunSQL(
            SQL,
            "DROP TRIGGER astrolift_notification_inbox_admission ON astrolift_operations_notification; "
            "DROP FUNCTION astrolift_notification_inbox_admit();",
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.UniqueConstraint(
                fields=("organization", "user", "inbox_sequence"), name="notification_inbox_sequence_unique"
            ),
        ),
    ]
