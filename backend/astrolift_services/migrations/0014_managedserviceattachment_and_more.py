import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0020_agent_secret_bindings_and_bundles"),
        ("astrolift_clusters", "0012_tenantcluster_oidc_auth_config"),
        ("astrolift_identity", "0020_resync_system_roles_agent_env_spec"),
        ("astrolift_lifecycle", "0028_deployment_github_deployment_id"),
        ("astrolift_registry", "0034_scope_repo_manifest_unique_to_organization"),
        ("astrolift_services", "0013_secretbundle_global_slug_constraint"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ManagedServiceAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
            ],
        ),
        migrations.RemoveConstraint(
            model_name="managedservice",
            name="msvc_unique_active_per_app_kind_name",
        ),
        migrations.RemoveConstraint(
            model_name="secretbundle",
            name="secret_bundle_slug_unique_active_per_team",
        ),
        migrations.RemoveConstraint(
            model_name="secretbundle",
            name="secret_bundle_slug_unique_active_per_org",
        ),
        migrations.AddField(
            model_name="managedservice",
            name="environment_name",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="project",
            field=models.ForeignKey(
                blank=True,
                help_text="Owning project for a shared resource; null for app-private resources.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="managed_services",
                to="astrolift_identity.project",
            ),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="tenant_cluster",
            field=models.ForeignKey(
                blank=True,
                help_text="Provisioning target for a project-owned resource.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="project_managed_services",
                to="astrolift_clusters.tenantcluster",
            ),
        ),
        migrations.AddField(
            model_name="secretbundle",
            name="project",
            field=models.ForeignKey(
                blank=True,
                help_text="Owning project for a project-shared bundle; null for team/org bundles.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="secret_bundles",
                to="astrolift_identity.project",
            ),
        ),
        migrations.AddField(
            model_name="secretbundle",
            name="tenant_cluster",
            field=models.ForeignKey(
                blank=True,
                help_text="Secrets backend used by a project-owned bundle.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="project_secret_bundles",
                to="astrolift_clusters.tenantcluster",
            ),
        ),
        migrations.AlterField(
            model_name="managedservice",
            name="app_environment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="managed_services",
                to="astrolift_lifecycle.appenvironment",
            ),
        ),
        migrations.AlterField(
            model_name="managedservice",
            name="registered_app",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="managed_services",
                to="astrolift_registry.registeredapp",
            ),
        ),
        migrations.AddConstraint(
            model_name="managedservice",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        app_environment__isnull=False,
                        project__isnull=True,
                        registered_app__isnull=False,
                        tenant_cluster__isnull=True,
                    )
                    | models.Q(
                        app_environment__isnull=True,
                        project__isnull=False,
                        registered_app__isnull=True,
                        tenant_cluster__isnull=False,
                    )
                ),
                name="msvc_exactly_one_owner_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="managedservice",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True, registered_app__isnull=False),
                fields=("registered_app", "kind", "name"),
                name="msvc_unique_active_per_app_kind_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="managedservice",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True, project__isnull=False),
                fields=("project", "kind", "name"),
                name="msvc_unique_active_per_project_kind_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="secretbundle",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(project__isnull=True, tenant_cluster__isnull=True)
                    | models.Q(project__isnull=False, tenant_cluster__isnull=False)
                ),
                name="secret_bundle_project_cluster_pair",
            ),
        ),
        migrations.AddConstraint(
            model_name="secretbundle",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True, project__isnull=True),
                fields=("team", "slug"),
                name="secret_bundle_slug_unique_active_per_team",
            ),
        ),
        migrations.AddConstraint(
            model_name="secretbundle",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    deleted_at__isnull=True,
                    project__isnull=True,
                    team__isnull=True,
                ),
                fields=("organization", "slug"),
                name="secret_bundle_slug_unique_active_per_org",
            ),
        ),
        migrations.AddConstraint(
            model_name="secretbundle",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True, project__isnull=False),
                fields=("project", "slug"),
                name="secret_bundle_slug_unique_active_per_project",
            ),
        ),
        migrations.AddField(
            model_name="managedserviceattachment",
            name="agent_environment_spec",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="project_managed_service_attachments",
                to="astrolift_agents.agentenvironmentspec",
            ),
        ),
        migrations.AddField(
            model_name="managedserviceattachment",
            name="app_environment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="project_managed_service_attachments",
                to="astrolift_lifecycle.appenvironment",
            ),
        ),
        migrations.AddField(
            model_name="managedserviceattachment",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Creator",
            ),
        ),
        migrations.AddField(
            model_name="managedserviceattachment",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="managedserviceattachment",
            name="managed_service",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="astrolift_services.managedservice",
            ),
        ),
        migrations.AddField(
            model_name="managedserviceattachment",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="managedserviceattachment",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(agent_environment_spec__isnull=True, app_environment__isnull=False)
                    | models.Q(agent_environment_spec__isnull=False, app_environment__isnull=True)
                ),
                name="msvc_attachment_exactly_one_consumer",
            ),
        ),
        migrations.AddConstraint(
            model_name="managedserviceattachment",
            constraint=models.UniqueConstraint(
                condition=models.Q(app_environment__isnull=False, deleted_at__isnull=True),
                fields=("managed_service", "app_environment"),
                name="msvc_attachment_unique_app_env",
            ),
        ),
        migrations.AddConstraint(
            model_name="managedserviceattachment",
            constraint=models.UniqueConstraint(
                condition=models.Q(agent_environment_spec__isnull=False, deleted_at__isnull=True),
                fields=("managed_service", "agent_environment_spec"),
                name="msvc_attachment_unique_agent_env",
            ),
        ),
    ]
