# Hand-written for #155 — crossing watermark for the daily cert-expiry
# monitor. Null on every existing row, which is the correct seed: the
# first sweep fires whichever reminder thresholds a cert has already
# crossed, then stamps the column so later sweeps stay quiet.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0030_deployment_manifest_resync_outcome"),
    ]

    operations = [
        migrations.AddField(
            model_name="customdomain",
            name="cert_expiry_checked_at",
            field=models.DateTimeField(
                blank=True,
                help_text='Watermark for the daily cert-expiry monitor (#155).  The sweep passes this to ``cert_expiry.evaluate`` as ``last_check_at`` so each 30 / 14 / 7-day reminder fires exactly once instead of re-paging every tick; null makes the next sweep fire every threshold already crossed.  Distinct from cert_metadata_refreshed_at, which tracks how fresh the *driver snapshot* is, not what has been alerted on.',
                null=True,
            ),
        ),
    ]
