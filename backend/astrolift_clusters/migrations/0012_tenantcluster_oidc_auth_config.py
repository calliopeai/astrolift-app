from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_clusters", "0011_tenantcluster_heartbeat"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="oidc_auth_config",
            field=models.JSONField(
                blank=True,
                default=None,
                null=True,
                help_text=(
                    "Dex + oauth2-proxy OIDC config for k8s_native edge auth. "
                    "Keys: discovery_url, client_id, cookie_secret, upstream_connector. "
                    "When set, every nginx Ingress rendered for this cluster carries "
                    "auth-url / auth-signin annotations pointing at the in-cluster "
                    "oauth2-proxy. Null = no auth gate. Dex + oauth2-proxy must have "
                    "been installed via the bootstrap recipe for this to function."
                ),
            ),
        ),
    ]
