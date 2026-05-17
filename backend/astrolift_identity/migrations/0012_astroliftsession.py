"""Add ``AstroliftSession`` sidecar (#480 / #495 / #498).

Sidecar to ``django_session`` populated by
``astrolift_identity.sessions.SessionTrackingMiddleware`` on every
authed request. Carries the per-session ``client_kind``,
``last_seen_at`` heartbeat timestamp, and explicit-revoke fields so
the platform can:

* surface a useful active-sessions list with kind + liveness signal
* revoke one session by id (#480)
* enforce per-user-per-kind concurrent-session quotas (#495)
* auto-prune sessions past the per-kind stale threshold (#498)

No data migration is needed for pre-existing django_session rows —
the middleware backfills the sidecar lazily on the next authed
request, defaulting ``client_kind=web`` for rows without an explicit
tag.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0011_merge_20260517_1933"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AstroliftSession",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
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
                    "session_key",
                    models.CharField(blank=True, db_index=True, default="", max_length=64),
                ),
                (
                    "client_kind",
                    models.CharField(
                        choices=[
                            ("web", "Web browser"),
                            ("cli", "Command-line tool"),
                            ("mobile", "Mobile app"),
                            ("browser_extension", "Browser extension"),
                            ("api_token", "API token"),
                        ],
                        db_index=True,
                        default="web",
                        max_length=32,
                    ),
                ),
                ("label", models.CharField(blank=True, default="", max_length=200)),
                ("last_seen_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_seen_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("last_seen_agent", models.CharField(blank=True, default="", max_length=512)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("revocation_reason", models.CharField(blank=True, default="", max_length=64)),
                (
                    "api_token",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sessions",
                        to="astrolift_identity.apitoken",
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
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="astrolift_sessions",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "revoked_by",
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
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="astrolift_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.AddIndex(
            model_name="astroliftsession",
            index=models.Index(
                fields=["user", "client_kind"],
                name="astrosess_user_kind_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="astroliftsession",
            index=models.Index(
                fields=["organization", "user"],
                name="astrosess_org_user_idx",
            ),
        ),
    ]
