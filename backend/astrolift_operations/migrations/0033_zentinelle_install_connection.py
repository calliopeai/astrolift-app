import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0032_zentinelle_gateway_url"),
    ]

    operations = [
        migrations.AlterField(
            model_name="zentinelleconnection",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Null for the install connection, which shared clusters register through (#1905).",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="zentinelle_connections",
                to="astrolift_identity.organization",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="zentinelleconnection",
            name="zentinelle_connection_one_live_per_org",
        ),
        migrations.AddConstraint(
            model_name="zentinelleconnection",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("organization",),
                name="zentinelle_connection_one_live_per_org",
                nulls_distinct=False,
            ),
        ),
    ]
