from __future__ import annotations

from django.contrib import admin

from astrolift_clusters.models import (
    ManagedDomain,
    ManagedServiceCatalogEntry,
    ProviderPlugin,
    ProviderPluginConfig,
    TenantCluster,
)


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(ProviderPlugin)
class ProviderPluginAdmin(_AllObjectsAdmin):
    list_display = ("slug", "version", "is_enabled", "deleted_at")
    list_filter = ("is_enabled",)
    search_fields = ("slug", "name")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(ProviderPluginConfig)
class ProviderPluginConfigAdmin(_AllObjectsAdmin):
    list_display = ("provider_plugin", "organization", "deleted_at")
    list_filter = ("organization",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(TenantCluster)
class TenantClusterAdmin(_AllObjectsAdmin):
    list_display = (
        "slug",
        "organization",
        "provider_plugin",
        "region",
        "is_active",
        "capabilities_probed_at",
    )
    list_filter = ("provider_plugin", "is_active")
    search_fields = ("name", "slug", "region")
    readonly_fields = ("guid", "created_at", "updated_at", "version", "capabilities_probed_at")


@admin.register(ManagedDomain)
class ManagedDomainAdmin(_AllObjectsAdmin):
    list_display = ("zone", "organization", "dns_driver", "default_for", "is_wildcard_managed", "deleted_at")
    list_filter = ("dns_driver", "default_for", "is_wildcard_managed")
    search_fields = ("zone",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")

    DNS_DRIVER_CHOICES = [
        ("route53", "AWS Route 53"),
        ("cloud_dns", "Google Cloud DNS"),
        ("azure_dns", "Azure DNS"),
    ]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        from django import forms

        if db_field.name == "dns_driver":
            kwargs["widget"] = forms.Select(choices=self.DNS_DRIVER_CHOICES)
        if db_field.name == "default_for":
            kwargs["widget"] = forms.Select(
                choices=ManagedDomain.DefaultFor.choices
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(ManagedServiceCatalogEntry)
class ManagedServiceCatalogEntryAdmin(_AllObjectsAdmin):
    list_display = ("kind", "variant", "provider_plugin", "is_default_for_kind")
    list_filter = ("kind", "provider_plugin", "is_default_for_kind")
    search_fields = ("kind", "variant", "display_name")
