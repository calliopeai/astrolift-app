from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_clusters', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenantcluster',
            name='delivery_mode',
            field=models.CharField(
                max_length=32,
                choices=[
                    ('direct_api', 'Direct Api'),
                    ('gitops_argocd', 'Gitops Argocd'),
                    ('gitops_flux', 'Gitops Flux'),
                    ('hybrid', 'Hybrid'),
                ],
                default='direct_api',
            ),
        ),
        migrations.AddField(
            model_name='tenantcluster',
            name='delivery_config',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
