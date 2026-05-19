# Hand-written for #742 — DomainRedirectRule rows hang off CustomDomain
# and back the AppDomain Redirects sub-section in the FE (#685).  The
# setDomainRedirects mutation replaces the full set per domain so the
# (custom_domain, priority) composite index is the resolver's read
# path; no unique constraints — duplicate priorities are allowed and
# the FE renders them in insertion order.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0017_environment_setting"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DomainRedirectRule",
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
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("http_to_https", "Http To Https"),
                            ("apex_to_www", "Apex To Www"),
                            ("www_to_apex", "Www To Apex"),
                            ("alias", "Alias"),
                            ("custom", "Custom"),
                        ],
                        max_length=32,
                    ),
                ),
                ("source_pattern", models.CharField(blank=True, default="", max_length=512)),
                ("destination_url", models.CharField(blank=True, default="", max_length=512)),
                (
                    "http_status",
                    models.IntegerField(
                        choices=[
                            (301, "Moved Permanently"),
                            (302, "Found"),
                            (307, "Temporary Redirect"),
                            (308, "Permanent Redirect"),
                        ],
                        default=301,
                    ),
                ),
                ("preserve_query_string", models.BooleanField(default=True)),
                ("priority", models.PositiveIntegerField(default=0)),
                (
                    "custom_domain",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="redirect_rules",
                        to="astrolift_lifecycle.customdomain",
                    ),
                ),
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
            ],
            options={"ordering": ["priority"]},
        ),
        migrations.AddIndex(
            model_name="domainredirectrule",
            index=models.Index(
                fields=["custom_domain", "priority"],
                name="redirect_domain_priority_idx",
            ),
        ),
    ]
