"""FormSubmission — one submitter's response to a FormDefinition.

Captures the snapshot the submitter saw (``form_version``) alongside
the payload so historical submissions don't drift when the form is
edited and re-published. ``submitter`` is null for anonymous public
submissions; ``submitter_email`` is the only PII we hang on to in
that case.

``status`` is a moderation lifecycle the operator can move through:
``new`` → ``reviewed`` / ``flagged`` / ``archived``. The transitions
are unrestricted within the live statuses (no admission constraints
beyond "must exist in the choices").

Soft-delete via :class:`BaseCoreModel` so an operator can hide a
spam submission without losing it. Indexes target the two query
shapes the UI actually runs:

* ``(form, -submitted_at)`` — the submissions tab paginates newest-first.
* ``(form, status, -submitted_at)`` — the FE allows status-filter.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class FormSubmission(BaseCoreModel):
    class Status(models.TextChoices):
        NEW = "new"
        REVIEWED = "reviewed"
        FLAGGED = "flagged"
        ARCHIVED = "archived"

    form = models.ForeignKey(
        "astrolift_forms.FormDefinition",
        related_name="submissions",
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="form_submissions",
        on_delete=models.CASCADE,
    )
    form_version = models.IntegerField(
        help_text="Form version the submitter saw — snapshotted on submit.",
    )
    payload = models.JSONField(
        default=dict,
        help_text="Submitter responses keyed by field name.",
    )
    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="form_submissions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="The authenticated submitter, or null for anonymous public submissions.",
    )
    submitter_email = models.EmailField(
        blank=True,
        default="",
        help_text="Submitter email for anonymous public submissions; blank when authenticated.",
    )
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["form", "-submitted_at"],
                name="fsub_form_recent_idx",
            ),
            models.Index(
                fields=["form", "status", "-submitted_at"],
                name="fsub_form_status_idx",
            ),
            models.Index(
                fields=["organization", "-submitted_at"],
                name="fsub_org_recent_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"FormSubmission(form={self.form_id}, submitted_at={self.submitted_at}, status={self.status})"
