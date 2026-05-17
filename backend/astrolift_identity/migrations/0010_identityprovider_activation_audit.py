"""IdP activation-audit columns (#467).

Two columns on ``IdentityProvider`` so the /settings/identity-provider
page can render a stable "Active since {date} by {operator}" caption
on the currently-active row:

* ``activated_at`` — DateTime, nullable. Stamped by the
  ``set_active_identity_provider`` mutation (and by
  ``create_identity_provider`` when ``set_active=True``). Distinct
  from ``updated_at`` so config edits to the discovery URL, client
  secret, etc. don't tick the caption forward.
* ``last_switched_by`` — FK to the user who last flipped this IdP to
  active. ``on_delete=SET_NULL`` so deleting the operator doesn't
  cascade away the IdP row.

Data backfill: for every Organization that currently has an
``identity_provider_id`` pointing at a row whose ``activated_at`` is
null, copy ``updated_at`` in. We don't know who originally flipped the
switch (the prior mutation didn't record it), so ``last_switched_by``
stays null for backfilled rows — the FE conditionally renders the
"by …" caption only when the field is populated.
"""

from __future__ import annotations

from django.conf import settings
from django.db import migrations, models


def backfill_activated_at(apps, schema_editor):
    Organization = apps.get_model("astrolift_identity", "Organization")
    IdentityProvider = apps.get_model("astrolift_identity", "IdentityProvider")

    active_idp_ids = list(
        Organization.objects.filter(identity_provider__isnull=False)
        .values_list("identity_provider_id", flat=True)
        .distinct()
    )
    if not active_idp_ids:
        return

    # Set ``activated_at = updated_at`` for the currently-active IdPs
    # that haven't been stamped yet. We iterate the small set (one
    # active IdP per org, capped) so each row gets its own
    # ``updated_at`` rather than a single migration-time wall clock.
    for idp in IdentityProvider.objects.filter(pk__in=active_idp_ids, activated_at__isnull=True):
        idp.activated_at = idp.updated_at
        idp.save(update_fields=["activated_at"])


def noop_reverse(apps, schema_editor):
    """Reverse leaves the column drop to the schema migration —
    nothing else to do; backfilled timestamps are discardable."""


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0009_apitoken_last_used_ip_agent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="identityprovider",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="identityprovider",
            name="last_switched_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_activated_at, reverse_code=noop_reverse),
    ]
