"""FormDefinition — versioned, org-scoped form schema.

Backs the `/forms/[slug]` UI shipped in #437 scope B. The shape of
the row mirrors what `frontend/graphql/forms/forms.queries.ts`
selects:

* ``schema`` — JSON Schema-like field tree (``{type, properties,
  required}`` + ``x-widget`` extensions for the visual builder).
* ``field_config`` — per-field UI-only knobs (placeholder, helper
  copy) that don't belong in the JSON Schema.
* ``logic_rules`` / ``scoring`` — declarative show/hide/calculate
  rules and per-field scoring used by the runtime renderer.
* ``form_type`` / ``is_public`` — categorisation (survey, intake,
  …) and the "public submission" affordance for anonymous submitters.
* ``status`` — ``draft`` | ``published`` | ``archived``. Lifecycle
  transitions are managed by the dedicated publish/archive mutations
  so we can stamp ``published_at`` exactly once.
* ``submission_count_cached`` — denormalised counter the list view
  reads without a per-row aggregate. Updated in the submit / delete
  paths so the counter stays close to reality without a periodic job.

Soft delete via :class:`BaseCoreModel`. Slug uniqueness is partial
(per-org, ``deleted_at IS NULL``) so a deleted slug can be re-used.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class FormDefinition(BaseCoreModel):
    """A versioned, org-scoped form definition."""

    class Status(models.TextChoices):
        DRAFT = "draft"
        PUBLISHED = "published"
        ARCHIVED = "archived"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="form_definitions",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=128, db_index=True)
    description = models.TextField(blank=True, default="")
    schema = models.JSONField(
        default=dict,
        help_text="JSON-Schema-like field tree consumed by the renderer.",
    )
    field_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-field UI knobs (placeholder, helper text) that aren't part of the JSON Schema.",
    )
    logic_rules = models.JSONField(
        default=list,
        blank=True,
        help_text="Declarative show/hide/calculate rules evaluated by the runtime renderer.",
    )
    scoring = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional per-field scoring map (e.g. quiz / risk scoring).",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    form_type = models.CharField(
        max_length=32,
        default="generic",
        help_text="Tag for filtering / templating (e.g. survey, intake, quiz).",
    )
    is_public = models.BooleanField(
        default=False,
        help_text="When true, submissions may come from anonymous users via the public submit page.",
    )
    schema_version = models.IntegerField(
        default=1,
        help_text=(
            "Form-level schema version (independent of the row's tracking version). "
            "Bumped on publish so historical submissions can reference the schema they were filled against. "
            "Surfaced on the GraphQL surface as ``version`` for the FE."
        ),
    )
    published_at = models.DateTimeField(null=True, blank=True)
    submission_count_cached = models.IntegerField(
        default=0,
        help_text="Denormalised submission count — updated in the submit / delete paths.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="form_definition_unique_active_slug_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status", "-updated_at"],
                name="form_def_org_status_upd_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"FormDefinition({self.organization_id}, {self.slug}, v{self.schema_version}, {self.status})"
