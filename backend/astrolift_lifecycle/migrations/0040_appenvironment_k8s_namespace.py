from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0039_deployment_secret_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="appenvironment",
            name="k8s_namespace",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "The Kubernetes namespace this environment renders into, when it "
                    "has one of its own (#1922). Blank means the app's namespace, "
                    "which is where every environment rendered before #1922 and where "
                    "each one that existed then still renders, so none of their "
                    "objects moves."
                    "\n\n"
                    "Set when the environment is created: a preview takes its "
                    "`PreviewEnvironment.namespace`, and an environment created on a "
                    "cluster where another environment of the app already renders "
                    "into the app namespace gets one of its own. Without it the two "
                    "wrote the same Deployments, Services and Secrets, and each "
                    "deploy overwrote the other environment's workloads."
                ),
                max_length=128,
            ),
        ),
    ]
