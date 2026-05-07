from __future__ import annotations

from django.contrib import admin

from astrolift_registry.models import RegisteredApp


@admin.register(RegisteredApp)
class RegisteredAppAdmin(admin.ModelAdmin):
    list_display = (
        "slug",
        "organization",
        "team",
        "project",
        "source_kind",
        "provisioning_status",
        "is_active",
        "deleted_at",
    )
    list_filter = ("source_kind", "provisioning_status", "is_active")
    search_fields = ("name", "slug", "source_repo")
    readonly_fields = ("guid", "created_at", "updated_at", "version")

    def get_queryset(self, request):
        return self.model.all_objects.all()
