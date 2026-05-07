"""
RoleBinding — binds a User (or a SCIM Group) to a Role on a scope.

Permission resolution at request time walks up App → Project → Team →
Org and unions every binding's role permissions, optionally bounded by
``expires_at`` for time-boxed grants.

Either ``user`` or ``group`` is set, never both. The ``scope_kind`` /
``scope_id`` pair points at one of Org/Team/Project/App; we use a
generic ``scope_id`` (BigInt) instead of a polymorphic FK because a
single index spans all four parents and the lookup cost is uniform.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models.base import BaseCoreModel


class RoleBinding(BaseCoreModel):
    class ScopeKind(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        PROJECT = "PROJECT"
        APP = "APP"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="role_bindings",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    group_external_id = models.CharField(max_length=255, blank=True, default="")
    role = models.ForeignKey(
        "astrolift_identity.Role",
        related_name="bindings",
        on_delete=models.PROTECT,
    )
    scope_kind = models.CharField(max_length=16, choices=ScopeKind.choices)
    scope_id = models.BigIntegerField()

    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="granted_role_bindings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    inherits = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(user__isnull=False, group_external_id="")
                    | models.Q(user__isnull=True) & ~models.Q(group_external_id="")
                ),
                name="rolebinding_user_xor_group",
            ),
            models.UniqueConstraint(
                fields=["user", "role", "scope_kind", "scope_id"],
                condition=models.Q(deleted_at__isnull=True, user__isnull=False),
                name="rolebinding_unique_user",
            ),
        ]
        indexes = [
            models.Index(
                fields=["scope_kind", "scope_id"],
                name="rb_scope_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if (self.user_id is None) == (not self.group_external_id):
            raise ValidationError("RoleBinding requires exactly one of user or group_external_id")
