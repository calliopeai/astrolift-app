"""Add ``app_client_id`` to SourceConnection.

Separates the GitHub App **Client ID** (string, e.g.
``Iv23lic8662KXwe4XKEI``) from the existing ``oauth_client_id`` column
which (for ``github_app_install`` rows) carries the numeric **App ID**
(e.g. ``3705068``).

The OAuth-dance URL (``/login/oauth/authorize?client_id=…``) and the
GitHub-recommended JWT ``iss`` claim both want the Client ID. The
webhook-signature path keeps using the App ID via ``oauth_client_id``.

NO BACKFILL: the Client ID is not derivable from any value we already
store (App ID, installation_id, owner_login, PEM). The operator pastes
it once per existing connection via the Settings UI. Existing rows are
left with ``app_client_id == ""`` so the frontend can flag them with
an inline 'Action required: add Client ID' banner.

Refs: calliopeai/astrolift-app#525.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_scm", "0008_sourceconnection_reauth_required"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourceconnection",
            name="app_client_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "GitHub App OAuth Client ID (e.g. Iv23lic8662KXwe4XKEI). "
                    "Required for github_oauth_app + github_app_install kinds. "
                    "Not derivable from existing data; operator enters it once."
                ),
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="sourceconnection",
            name="oauth_client_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Non-secret provider identifier. For github_oauth_app + "
                    "gitlab_oauth_app: the OAuth Client ID. For "
                    "github_app_install: the numeric App ID (NOT the Client "
                    "ID — see app_client_id for the OAuth Client ID)."
                ),
                max_length=256,
            ),
        ),
    ]
