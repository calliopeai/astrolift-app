from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_clusters", "0009_merge_0008_manageddomain_provision_0008_tenantcluster_node"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="alb_auth_config",
            field=models.JSONField(
                blank=True,
                default=None,
                null=True,
                help_text=(
                    "Cognito IDP config for ALB authenticate-cognito rules. "
                    "Keys: user_pool_arn, user_pool_client_id, user_pool_domain. "
                    "When set, every ALB Ingress rendered for this cluster carries "
                    "the Cognito auth annotations. Null = no auth gate."
                ),
            ),
        ),
    ]
