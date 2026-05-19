"""EnvironmentSetting — per-environment key/value overrides (#744)."""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class EnvironmentSetting(BaseCoreModel):
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="settings",
        on_delete=models.CASCADE,
    )
    key = models.CharField(max_length=256)
    value = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("app_environment", "key"),
                name="env_setting_unique_active_env_key",
            )
        ]
        indexes = [
            models.Index(fields=["app_environment", "key"], name="env_setting_env_key_idx"),
        ]
