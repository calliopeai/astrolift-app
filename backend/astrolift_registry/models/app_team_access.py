"""
AppTeamAccess — multi-team access to a RegisteredApp.

Supplements the single ``RegisteredApp.team`` FK (the app's primary /
"home" team, kept for back-compat with existing scope chains) with a
join row per *additional* team that should see + operate on the app.

Each row carries an access level so a team can be granted read-only
visibility (``VIEWER``), write + deploy (``DEPLOYER``), or full
control including team-access management (``OWNER``). The home-team
row is materialized as OWNER by the registration / move flows so the
join table is the canonical answer to "which teams can reach this
app?" — the FK is only the pointer at *one* of those rows.

Soft-deletes follow the workspace convention: the unique constraint
is partial on ``deleted_at IS NULL`` so a revoked grant doesn't
block re-granting.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AppTeamAccess(BaseCoreModel):
    class AccessLevel(models.TextChoices):
        VIEWER = "viewer"
        DEPLOYER = "deployer"
        OWNER = "owner"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        on_delete=models.CASCADE,
        related_name="team_accesses",
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        on_delete=models.CASCADE,
        related_name="app_accesses",
    )
    access_level = models.CharField(
        max_length=16,
        choices=AccessLevel.choices,
        default=AccessLevel.DEPLOYER,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "team"],
                condition=models.Q(deleted_at__isnull=True),
                name="app_team_access_unique_active",
            ),
        ]
        indexes = [
            models.Index(fields=["registered_app"], name="apt_access_app_idx"),
            models.Index(fields=["team"], name="apt_access_team_idx"),
        ]

    def __str__(self) -> str:
        return f"AppTeamAccess(app={self.registered_app_id}, team={self.team_id}, {self.access_level})"
