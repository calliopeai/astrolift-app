from __future__ import annotations

from django.contrib import admin

from astrolift_registry.models import Container, RegisteredApp, Template, Workload


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(RegisteredApp)
class RegisteredAppAdmin(_AllObjectsAdmin):
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


@admin.register(Workload)
class WorkloadAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "slug", "kind", "is_public", "replicas")
    list_filter = ("kind", "is_public")


@admin.register(Container)
class ContainerAdmin(_AllObjectsAdmin):
    list_display = ("workload", "name", "is_primary", "image_ref")
    list_filter = ("is_primary",)


@admin.register(Template)
class TemplateAdmin(_AllObjectsAdmin):
    list_display = ("name", "slug", "organization", "source_repo")
