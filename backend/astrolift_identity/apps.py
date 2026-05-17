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

        # #487 — register the default password verifier so the
        # elevateAdminSession mutation works out of the box on
        # local-login installs. SSO-only installs override at
        # startup via ``register_credential_verifier`` with a
        # composed verifier covering their IdP-specific methods
        # (WebAuthn assertion, OTP, magic-link).
        from astrolift_identity.session_elevation import (
            default_password_verifier,
            register_credential_verifier,
        )

        register_credential_verifier(default_password_verifier)
