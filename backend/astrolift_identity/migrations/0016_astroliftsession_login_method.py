"""Add ``AstroliftSession.login_method`` (#526).

The step-up auth gate (#487) shipped with a single credential verifier
— password against the Django User row. SSO-only installs (Auth0 /
Cognito federation) have unusable password hashes per Django's
convention (``set_unusable_password``), so the password prompt is
unfulfillable for those users. Production traced the gap to a
force-redeploy attempt by an SSO user that surfaced an empty modal.

This column tells the step-up gate *how* the session was minted so
the FE can branch the re-auth UI: SSO sessions get a
"Re-authenticate with your provider" button that bounces through the
IdP with ``prompt=login&max_age=0``; local-login sessions keep the
password form. The default ``password`` matches the pre-#526 world
where every authenticated session was implicitly password-backed —
no backfill needed; the session-tracking middleware re-stamps the
column on the next authed request from the session bag.

No data migration: the per-row override is set on the next touch by
:func:`astrolift_identity.sessions.record_session`.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0015_device_attestation"),
    ]

    operations = [
        migrations.AddField(
            model_name="astroliftsession",
            name="login_method",
            field=models.CharField(
                choices=[
                    ("password", "Username + password"),
                    ("sso", "External IdP (Auth0 / Cognito / OIDC)"),
                    ("magic_link", "Magic link / one-time email"),
                    ("webauthn", "Passkey / WebAuthn"),
                ],
                db_index=True,
                default="password",
                max_length=32,
            ),
        ),
    ]
