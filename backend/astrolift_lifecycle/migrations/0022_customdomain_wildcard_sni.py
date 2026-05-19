"""Add wildcard + SNI fields to ``CustomDomain`` (#753).

Two non-destructive ADD COLUMNs:

* ``is_wildcard`` — distinguishes ``*.hostname`` TLS coverage from
  single-host. Required so the renderer can emit the correct SAN set
  on the ingress (``hostname`` + ``*.hostname``) and so the cert-
  validation workflow knows to enforce the CA's wildcard rules
  (DNS-01 only, no HTTP-01).
* ``sni_cert_ref`` — provider-specific cert identifier the operator
  pins for SNI on this hostname. Empty default means the renderer
  picks via its default cert-matching rules; populated when the
  operator needs cross-cert SNI control (mixed EV / DV / wildcard).

Existing rows are unaffected: ``is_wildcard=False`` is the historical
behavior (single-host issuance via DNS-TXT / HTTP-01); ``sni_cert_ref``
defaults to ``""`` which matches the renderer's auto-pick code path.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0021_previewenvironment_is_manual"),
    ]

    operations = [
        migrations.AddField(
            model_name="customdomain",
            name="is_wildcard",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True when this domain covers ``*.hostname`` "
                    "(wildcard TLS). Issued certificate must carry both "
                    "the apex and the ``*.<hostname>`` SAN. Wildcard "
                    "issuance requires DNS-01 validation — HTTP-01 / "
                    "DNS-TXT can't satisfy CA wildcard policy, so the "
                    "``addWildcardDomain`` mutation pins "
                    "``validation_method`` to ``dns_01``."
                ),
            ),
        ),
        migrations.AddField(
            model_name="customdomain",
            name="sni_cert_ref",
            field=models.CharField(
                max_length=255,
                blank=True,
                default="",
                help_text=(
                    "Provider-specific certificate identifier the "
                    "renderer presents for SNI on this hostname (ACM "
                    "ARN, GCP managed-cert resource name, Azure Key "
                    "Vault cert URI, etc.). Empty when the platform "
                    "manages cert selection automatically — the "
                    "renderer falls back to its default cert-matching "
                    "rules. Operators set this when they need to pin "
                    "a specific cert across multi-domain SNI scenarios "
                    "(e.g., an EV cert on the apex with a wildcard "
                    "cert on subdomains)."
                ),
            ),
        ),
    ]
