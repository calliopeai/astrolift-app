from __future__ import annotations

from django.contrib import admin

from astrolift_services.models import (
    AppSecretBundleRef,
    ManagedService,
    ManagedServiceBinding,
    SecretBundle,
    SecretChangeApproval,
    SecretChangeProposal,
)


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(ManagedService)
class ManagedServiceAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "kind", "name", "variant", "status")
    list_filter = ("kind", "status")


@admin.register(ManagedServiceBinding)
class ManagedServiceBindingAdmin(_AllObjectsAdmin):
    list_display = ("managed_service", "env_key", "is_secret")
    list_filter = ("is_secret",)


@admin.register(SecretBundle)
class SecretBundleAdmin(_AllObjectsAdmin):
    list_display = ("name", "organization", "team")
    search_fields = ("name", "slug")


@admin.register(AppSecretBundleRef)
class AppSecretBundleRefAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "app_environment", "secret_bundle", "prefix")


@admin.register(SecretChangeProposal)
class SecretChangeProposalAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "environment_name", "op", "status", "proposer", "expires_at")
    list_filter = ("status", "op")
    search_fields = ("registered_app__slug", "environment_name")


@admin.register(SecretChangeApproval)
class SecretChangeApprovalAdmin(_AllObjectsAdmin):
    list_display = ("proposal", "approver", "decision", "decided_at")
    list_filter = ("decision",)
