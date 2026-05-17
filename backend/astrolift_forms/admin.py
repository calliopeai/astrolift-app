from __future__ import annotations

from django.contrib import admin

from astrolift_forms.models import FormDefinition, FormSubmission


@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "organization",
        "status",
        "schema_version",
        "form_type",
        "is_public",
        "submission_count_cached",
        "published_at",
    )
    list_filter = ("status", "form_type", "is_public")
    search_fields = ("name", "slug", "description")
    readonly_fields = (
        "guid",
        "submission_count_cached",
        "published_at",
        "created_at",
        "updated_at",
        "deleted_at",
    )


@admin.register(FormSubmission)
class FormSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "form",
        "submitter",
        "submitter_email",
        "form_version",
        "status",
        "submitted_at",
        "source_ip",
    )
    list_filter = ("status",)
    search_fields = ("submitter_email", "form__slug", "form__name")
    readonly_fields = (
        "guid",
        "form",
        "form_version",
        "submitted_at",
        "source_ip",
        "user_agent",
        "submitter",
        "submitter_email",
    )

    def has_add_permission(self, *args, **kwargs):
        return False
