"""Add ``last_used_ip`` + ``last_used_agent`` to DeployToken (#425).

Pairs with the auth-time touch logic in
``astrolift_lifecycle.deploy_tokens.touch_deploy_token``: when a CI
runner request arrives carrying an ``alft_dt_`` bearer the middleware
stamps these columns so operators can correlate a leaked deploy token
with its last caller. Mirrors the equivalent ApiToken migration
(#428, ``astrolift_identity.0009_apitoken_last_used_ip_agent``).
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0012_previewenvironment_ttl_until"),
    ]

    operations = [
        migrations.AddField(
            model_name="deploytoken",
            name="last_used_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deploytoken",
            name="last_used_agent",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
