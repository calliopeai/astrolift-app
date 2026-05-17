"""Device attestation columns + AttestationChallenge model (#496).

Adds five columns to ``AstroliftSession`` so a mobile session can
carry a verified App Attest / Play Integrity outcome, and a new
``AttestationChallenge`` table for the server-issued nonces the
device incorporates into its signed attestation.

No backfill: every pre-existing session row defaults to
``attestation_kind='none'`` + ``attestation_trust_level='not_attested'``,
which is the correct interpretation for browser / CLI / API-token
sessions and for mobile sessions issued before this migration. The
mobile client surfaces the per-install ``REQUIRE_ATTESTATION_FOR_MOBILE``
flag and prompts the user to re-attest if the policy requires it.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import astrolift_identity.models.attestation_challenge


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0014_merge_session_branches"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="astroliftsession",
            name="attestation_kind",
            field=models.CharField(
                choices=[
                    ("none", "Not attested"),
                    ("ios_appattest", "iOS App Attest"),
                    ("android_play_integrity", "Android Play Integrity"),
                ],
                db_index=True,
                default="none",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="astroliftsession",
            name="attestation_trust_level",
            field=models.CharField(
                choices=[
                    ("not_attested", "Not attested"),
                    ("genuine", "Genuine"),
                    ("unknown", "Unknown"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="not_attested",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="astroliftsession",
            name="attestation_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="astroliftsession",
            name="attestation_public_key",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="astroliftsession",
            name="attestation_counter",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="astroliftsession",
            name="attestation_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddIndex(
            model_name="astroliftsession",
            index=models.Index(
                fields=["attestation_kind", "attestation_trust_level"],
                name="astrosess_attest_idx",
            ),
        ),
        migrations.AlterField(
            model_name="astroliftsession",
            name="id",
            field=models.BigAutoField(
                auto_created=True,
                primary_key=True,
                serialize=False,
                verbose_name="ID",
            ),
        ),
        migrations.CreateModel(
            name="AttestationChallenge",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "nonce",
                    models.CharField(
                        default=astrolift_identity.models.attestation_challenge._make_nonce,
                        max_length=64,
                        unique=True,
                    ),
                ),
                ("kind", models.CharField(db_index=True, max_length=32)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attestation_challenges",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="attestationchallenge",
            index=models.Index(
                fields=["user", "kind"],
                name="attestchal_user_kind_idx",
            ),
        ),
    ]
