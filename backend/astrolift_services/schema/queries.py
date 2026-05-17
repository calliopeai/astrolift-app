"""GraphQL queries for app secrets, secret bundles, managed services."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_manifest.env_injection import envelope_keys_for
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretBundleRef,
    ManagedService,
    SecretBundle,
)
from astrolift_services.schema.types import (
    AppSecretBundleAttachmentType,
    AppSecretType,
    ManagedServiceType,
    SecretBundleType,
    attachment_to_type,
    managed_service_to_type,
    secret_bundle_to_type,
    secret_editor_from_user,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


def _secret_id(*, source: str, key: str, env: str) -> str:
    return f"{source}:{env}:{key}"


def _bundle_key_count(bundle) -> int:
    """Best-effort count of keys the bundle is expected to project.

    Bundle values themselves live in the platform secrets backend.
    Until the SecretsBackend client surfaces the materialized key set,
    we infer from the manifest's declared envelope when known; 0
    otherwise — the UI shows '?' in that case so operators know the
    count is unavailable rather than a confirmed zero.
    """
    # The bundle stores a backend_ref like `vault:/acme/prod` —
    # nothing platform-side enumerates the key set without a live
    # secrets-backend round-trip. Return 0 until #424 backend gap
    # ticket lands the cluster-side reflector.
    return 0


def _list_app_secrets(*, app, env_names: list[str]) -> list[AppSecretType]:
    """Compose the merged secret view across:
    - manifest [env] literals (app-wide → repeated per env)
    - per-env AppSecretBundleRef key prefix (we don't fetch values
      from the secrets backend; we surface the bundle slug + prefix
      so the UI shows 'attached')
    - per-env ManagedService bindings (envelope keys per binding)
    """
    out: list[AppSecretType] = []

    # 1. Literals from the staged-or-source manifest. Reads from
    # manifest_raw_staged when set so the UI reflects unsaved
    # edits; otherwise from manifest_raw.
    raw_text = app.manifest_raw_staged or app.manifest_raw or ""
    literals = read_app_env(raw_text)
    literal_editor = secret_editor_from_user(app.updated_by)
    for env_name in env_names:
        for key in sorted(literals):
            out.append(
                AppSecretType(
                    id=_secret_id(source="literal", key=key, env=env_name),
                    key=key,
                    environment_name=env_name,
                    source="literal",
                    bundle_slug="",
                    managed_service_kind="",
                    is_masked=True,
                    last_edited_at=app.updated_at,
                    last_edited_by=literal_editor,
                )
            )

    # 2. Bundle attachments (per env)
    refs = AppSecretBundleRef.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
        app_environment__name__in=env_names,
    ).select_related("secret_bundle", "app_environment", "updated_by")
    for ref in refs:
        # Bundle keys live in the secrets backend; we only know the
        # bundle attachment exists. Surface a single placeholder row
        # per attachment so the UI can show 'bundle: stripe-prod'
        # without us fetching values out-of-cluster.
        out.append(
            AppSecretType(
                id=_secret_id(
                    source="bundle",
                    key=ref.secret_bundle.slug,
                    env=ref.app_environment.name,
                ),
                key=f"{ref.prefix or ''}*" if ref.prefix else "*",
                environment_name=ref.app_environment.name,
                source="bundle",
                bundle_slug=ref.secret_bundle.slug,
                managed_service_kind="",
                is_masked=True,
                last_edited_at=ref.updated_at,
                last_edited_by=secret_editor_from_user(ref.updated_by),
            )
        )

    # 3. Managed-service envelopes (per binding)
    services = ManagedService.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
        app_environment__name__in=env_names,
    ).select_related("app_environment", "updated_by")
    for svc in services:
        env_name = svc.app_environment.name
        svc_editor = secret_editor_from_user(svc.updated_by)
        for key in envelope_keys_for(svc.kind):
            out.append(
                AppSecretType(
                    id=_secret_id(
                        source="managed_service",
                        key=f"{svc.name}.{key}",
                        env=env_name,
                    ),
                    key=key,
                    environment_name=env_name,
                    source="managed_service",
                    bundle_slug="",
                    managed_service_kind=svc.kind,
                    is_masked=True,
                    last_edited_at=svc.updated_at,
                    last_edited_by=svc_editor,
                )
            )

    return out


@strawberry.type
class ServicesQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_secrets(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[AppSecretType]:
        """List secret references visible to the app at runtime.

        ``environment_name`` filter narrows to one env; omitted
        returns the union across all envs."""
        app = RegisteredApp.objects.select_related("updated_by").filter(slug=app_slug).first()
        if app is None:
            return []
        if environment_name:
            env_names = [environment_name]
        else:
            env_names = list(
                AppEnvironment.objects.filter(
                    registered_app=app,
                    deleted_at__isnull=True,
                ).values_list("name", flat=True),
            )
            if not env_names:
                env_names = ["preview"]
        return _list_app_secrets(app=app, env_names=env_names)

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_secret_bundles(
        self,
        info: Info,
    ) -> list[SecretBundleType]:
        """All secret bundles in the calling tenant."""
        qs = (
            SecretBundle.objects.select_related("organization", "team")
            .filter(deleted_at__isnull=True)
            .order_by("-created_at")[:200]
        )
        return [secret_bundle_to_type(b) for b in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_secret_bundle_attachments(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[AppSecretBundleAttachmentType]:
        """Bundles attached to the app, grouped by env.

        Merge order is creation-order within an (app, env) group
        (lowest-first). App-local literals always win on collision
        regardless of merge_order — this column is just a debugging
        aid for the operator to read precedence at a glance."""
        qs = (
            AppSecretBundleRef.objects.select_related(
                "registered_app",
                "secret_bundle",
                "secret_bundle__team",
                "app_environment",
            )
            .filter(
                registered_app__slug=app_slug,
                deleted_at__isnull=True,
            )
            .order_by("app_environment__name", "created_at")
        )
        if environment_name:
            qs = qs.filter(app_environment__name=environment_name)
        out: list[AppSecretBundleAttachmentType] = []
        env_seq: dict[str, int] = {}
        for ref in qs:
            env_name = ref.app_environment.name
            order = env_seq.get(env_name, 0)
            env_seq[env_name] = order + 1
            out.append(
                attachment_to_type(
                    ref,
                    key_count=_bundle_key_count(ref.secret_bundle),
                    merge_order=order,
                )
            )
        return out

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_managed_services(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[ManagedServiceType]:
        qs = ManagedService.objects.select_related("registered_app", "app_environment").filter(
            registered_app__slug=app_slug,
            deleted_at__isnull=True,
        )
        if environment_name:
            qs = qs.filter(app_environment__name=environment_name)
        return [managed_service_to_type(s) for s in qs]
