"""Proof of control for a registered zone name that already exists (#1931).

``createManagedDomain`` created a row for any zone name the caller typed,
whether or not the platform was about to create that hosted zone. #2025 and
#2033 pinned every DNS write to a zone the platform itself created
(``provision_zone_id``), but a row naming a zone that already existed --
supplied via ``dns_config``'s ``zone_id``, or a plain name collision -- still
resolved by name and got real DNS writes, cert issuance, and hostnames.

``verification_state`` starts a row at ``pending`` for exactly that case; the
resolution helpers (``resolve_managed_domain`` / ``managed_domain_for_zone``)
now treat a pending row as unregistered. ``verification_token`` is the value
the caller publishes as ``_astrolift-challenge.<zone>`` TXT;
``verifyManagedDomain`` checks it over public DNS and stamps ``verified_at``.

No backfill: existing rows default to ``not_required`` and keep working
exactly as before, the same choice #2025's migration made for
``provision_zone_id``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_clusters", "0018_manageddomain_provision_zone_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="manageddomain",
            name="verification_state",
            field=models.CharField(
                choices=[("not_required", "Not Required"), ("pending", "Pending"), ("verified", "Verified")],
                default="not_required",
                help_text="Proof-of-control state for this row's zone name (#1931). Set to 'pending' at registration when the zone name already exists in the DNS provider or the caller supplied config for an existing zone, instead of one the platform is about to create. A 'pending' row is treated as unregistered by resolve_managed_domain / managed_domain_for_zone: no DNS write, cert issuance or ingress may use its zone until the caller publishes the TXT challenge and verifyManagedDomain flips this to 'verified'.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="manageddomain",
            name="verification_token",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Random value the caller publishes as `_astrolift-challenge.<zone>` TXT to prove control of the zone (#1931). Blank when verification_state is 'not_required'.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="manageddomain",
            name="verified_at",
            field=models.DateTimeField(
                blank=True, help_text="When verification_state last flipped to 'verified' (#1931).", null=True
            ),
        ),
    ]
