"""
Member — User ↔ scope (Org / Team / Project / App).

A user may belong to multiple scopes; each Member row represents one
attachment. ``is_active`` is the lifecycle column the spec calls out
in §8.4 (pending_invite → pending_first_login → active → suspended →
deactivated). The lifecycle is a string for now to keep migrations
trivial; it gates feature access in higher layers.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class Member(BaseCoreModel):
    class ScopeKind(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        PROJECT = "PROJECT"
        APP = "APP"

    class Lifecycle(models.TextChoices):
        PENDING_INVITE = "pending_invite"
        PENDING_FIRST_LOGIN = "pending_first_login"
        ACTIVE = "active"
        SUSPENDED = "suspended"
        DEACTIVATED = "deactivated"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="astrolift_memberships",
        on_delete=models.CASCADE,
    )
    scope_kind = models.CharField(max_length=16, choices=ScopeKind.choices)
    scope_id = models.BigIntegerField()
    is_active = models.BooleanField(default=True)
    lifecycle = models.CharField(
        max_length=32,
        choices=Lifecycle.choices,
        default=Lifecycle.ACTIVE,
    )
    joined_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "scope_kind", "scope_id"],
                condition=models.Q(deleted_at__isnull=True),
                name="member_unique_active",
            ),
        ]
        indexes = [
            models.Index(fields=["scope_kind", "scope_id"], name="member_scope_idx"),
            models.Index(fields=["user", "scope_kind"], name="member_user_kind_idx"),
        ]
