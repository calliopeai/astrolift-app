"""Record the hosted zone the platform created for a managed domain (#1931).

The DNS driver found zones by name, and ``dns_config`` (where the zone id was
kept) is tenant-editable, so teardown could delete, and record writes could
land in, a same-named hosted zone the row never owned. The platform now
writes this column when it creates the zone and pins every operation to it.

No backfill: an existing row's ``dns_config`` zone id may have been edited by
its tenant, so it is not promoted to proof of ownership. Such rows keep
working for record writes by name, and their teardown leaves the hosted zone
in place for the operator to remove.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_clusters", "0017_clusterbootstraprun_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="manageddomain",
            name="provision_zone_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Hosted zone the platform created for this row. Platform-written only, never from dns_config "
                    "(tenant-editable): DNS writes pin to it, and teardown deletes only it (#1931)."
                ),
                max_length=255,
            ),
        ),
    ]
