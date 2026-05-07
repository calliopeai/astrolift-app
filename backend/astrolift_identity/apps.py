from django.apps import AppConfig


class AstroliftIdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "astrolift_identity"
    label = "astrolift_identity"
    verbose_name = "Astrolift Identity"

    def ready(self) -> None:
        # Register the RoleBinding-backed permission resolver so
        # @require_permission gates against real RBAC instead of the
        # deny-by-default placeholder from P0.4.
        from astrolift_identity.permission_resolver import resolve
        from core.permissions import register_permission_resolver

        register_permission_resolver(resolve)
