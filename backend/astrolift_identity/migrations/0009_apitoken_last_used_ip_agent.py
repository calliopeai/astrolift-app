"""Add ``last_used_ip`` + ``last_used_agent`` to ApiToken (#428).

Pairs with the auth-time touch logic in
``astrolift_identity.api_tokens.touch_token``: when an authed request
arrives carrying an API token, we stamp these three columns
(``last_used_at`` already existed) so operators can correlate a leaked
token with its last caller.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_identity", "0008_resync_system_roles_cluster_manage"),
    ]

    operations = [
        migrations.AddField(
            model_name="apitoken",
            name="last_used_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="last_used_agent",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
