"""The install's generated identity (#479, #2161).

One row, written the first time anything asks for the id when
``ASTROLIFT_INSTALL_ID`` is unset (see ``core.install_identity``). It is
protocol state, not a business object: nothing renames or deletes it, so it
has no soft-delete lifecycle.
"""

from __future__ import annotations

from django.db import models


class InstallIdentity(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    install_id = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="install_identity_single_row"),
        ]

    def __str__(self) -> str:
        return self.install_id


__all__ = ["InstallIdentity"]
