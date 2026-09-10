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
        from astrolift_identity.permission_resolver import granted_scopes, resolve
        from core.permissions import (
            register_granted_scopes_provider,
            register_permission_resolver,
        )

        register_permission_resolver(resolve)
        # The dual of the resolver: which scopes a permission is held at.
        # Backs every ``any_scope=True`` gate and its row filter (#1717).
        register_granted_scopes_provider(granted_scopes)

        # #487 / #526 — register the default password verifier composed
        # with the SSO stub so the elevateAdminSession mutation works
        # out of the box on local-login installs and surfaces a typed
        # deny (not a silent pass) when a misconfigured FE tries to
        # post ``method=sso`` directly. The real SSO step-up ceremony
        # runs through ``astrolift_identity.step_up_sso`` — not the
        # mutation — so the SSO verifier is intentionally deny-only.
        from astrolift_identity.session_elevation import (
            compose_verifiers,
            default_password_verifier,
            default_sso_verifier,
            register_credential_verifier,
        )

        register_credential_verifier(compose_verifiers(default_password_verifier, default_sso_verifier))
