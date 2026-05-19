# Hand-written for #740 — DomainPathRoute rows hang off CustomDomain
# and back the path-based routing sub-section of the Domains page (#686).
# The setDomainPathRoutes mutation replaces the full set per domain so the
# (custom_domain, priority) composite index is the resolver's read path;
# no unique constraints — duplicate priorities are allowed.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0018_domain_redirect_rule"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DomainPathRoute",
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
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("path_prefix", models.CharField(max_length=512)),
                ("target_workload_slug", models.CharField(max_length=128)),
                ("target_port", models.PositiveIntegerField()),
                ("strip_prefix", models.BooleanField(default=False)),
                ("priority", models.PositiveIntegerField(default=0)),
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
                    "custom_domain",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="path_routes",
                        to="astrolift_lifecycle.customdomain",
                    ),
                ),
            ],
            options={
                "ordering": ["priority"],
            },
        ),
        migrations.AddIndex(
            model_name="domainpathroute",
            index=models.Index(
                fields=["custom_domain", "priority"],
                name="path_route_domain_priority_idx",
            ),
        ),
    ]
