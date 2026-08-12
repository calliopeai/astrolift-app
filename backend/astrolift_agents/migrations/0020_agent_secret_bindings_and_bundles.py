import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0019_agenttask_callback_token_hash"),
        ("astrolift_services", "0012_managedservice_kind_faas"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentSecretBindingOverride",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True
                    ),
                ),
                ("env_var", models.CharField(max_length=255)),
                ("uri", models.CharField(blank=True, default="", max_length=512)),
                ("removed", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Creator",
                    ),
                ),
                (
                    "deleted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "environment_spec",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="secret_binding_overrides",
                        to="astrolift_agents.agentenvironmentspec",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AgentSecretBundleRef",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True
                    ),
                ),
                ("environment", models.CharField(blank=True, default="default", max_length=64)),
                ("prefix", models.CharField(blank=True, default="", max_length=64)),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Creator",
                    ),
                ),
                (
                    "deleted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "environment_spec",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="secret_bundle_refs",
                        to="astrolift_agents.agentenvironmentspec",
                    ),
                ),
                (
                    "secret_bundle",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="agent_refs",
                        to="astrolift_services.secretbundle",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="agentsecretbindingoverride",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("environment_spec", "env_var"),
                name="agent_secret_binding_override_unique_active",
            ),
        ),
        migrations.AddIndex(
            model_name="agentsecretbindingoverride",
            index=models.Index(fields=["environment_spec", "env_var"], name="agent_secret_override_idx"),
        ),
        migrations.AddConstraint(
            model_name="agentsecretbundleref",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("environment_spec", "environment", "secret_bundle"),
                name="agent_secret_bundle_ref_unique_active",
            ),
        ),
        migrations.AddIndex(
            model_name="agentsecretbundleref",
            index=models.Index(
                fields=["environment_spec", "environment", "position"], name="agent_secret_bundle_order_idx"
            ),
        ),
    ]
