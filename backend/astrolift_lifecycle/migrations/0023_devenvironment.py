"""Create the ``DevEnvironment`` table for the App Builder integration (#767, #768).

Hand-written (no makemigrations available in this environment) so the
schema matches the model declared in
``astrolift_lifecycle/models/dev_environment.py``. Mirrors the field
shape every ``BaseCoreModel`` subclass uses (UUIDv7 ``guid``, integer
``version``, tracking columns + soft-delete + tracking FKs).

Depends on the heads of every app the model touches so the FK targets
exist at apply time:

* ``astrolift_lifecycle`` 0022 — the immediate prior migration on this app.
* ``astrolift_clusters`` 0007 — the head; ``tenant_cluster`` FK target.
* ``astrolift_registry`` 0020 — the head; ``promoted_app`` FK target.
* ``astrolift_operations`` 0015 — the head; ``workflow_run`` FK target.
* ``settings.AUTH_USER_MODEL`` — the ``creator`` + tracking FKs target.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import astrolift_lifecycle.models.dev_environment
import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0022_customdomain_wildcard_sni"),
        ("astrolift_clusters", "0007_clusterbootstraprun"),
        ("astrolift_registry", "0020_workload_volumes"),
        ("astrolift_operations", "0015_alertrule_managed_service"),
        ("astrolift_identity", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DevEnvironment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                (
                    "runtime",
                    models.CharField(
                        choices=[
                            ("python", "Python"),
                            ("node", "Node"),
                            ("ruby", "Ruby"),
                            ("go", "Go"),
                            ("static", "Static"),
                        ],
                        default="python",
                        max_length=16,
                    ),
                ),
                (
                    "runtime_version",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                (
                    "start_command",
                    models.CharField(blank=True, default="", max_length=512),
                ),
                ("port", models.PositiveIntegerField(default=8080)),
                ("env_vars", models.JSONField(blank=True, default=dict)),
                (
                    "resource_profile",
                    models.CharField(default="small", max_length=32),
                ),
                ("files", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("creating", "Creating"),
                            ("running", "Running"),
                            ("syncing", "Syncing"),
                            ("promoting", "Promoting"),
                            ("failed", "Failed"),
                            ("torn_down", "Torn Down"),
                        ],
                        default="creating",
                        max_length=16,
                    ),
                ),
                (
                    "preview_url",
                    models.URLField(blank=True, default=""),
                ),
                (
                    "namespace",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "ttl_until",
                    models.DateTimeField(
                        default=astrolift_lifecycle.models.dev_environment._default_dev_ttl_until,
                    ),
                ),
                ("torn_down_at", models.DateTimeField(blank=True, null=True)),
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
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dev_environments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dev_environments",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_environments",
                        to="astrolift_identity.team",
                    ),
                ),
                (
                    "tenant_cluster",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_environments",
                        to="astrolift_clusters.tenantcluster",
                    ),
                ),
                (
                    "promoted_app",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_dev_environment",
                        to="astrolift_registry.registeredapp",
                    ),
                ),
                (
                    "workflow_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_environments",
                        to="astrolift_operations.workflowrun",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
