"""GraphQL queries for app secrets, secret bundles, managed services."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_manifest.env_injection import envelope_keys_for
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretBundleRef,
    ManagedService,
    SecretBundle,
    SecretChangeProposal,
)
from astrolift_services.schema.types import (
    AppSecretBundleAttachmentType,
    AppSecretType,
    ManagedServiceObjectsType,
    ManagedServiceObjectType,
    ManagedServiceQueueDepthType,
    ManagedServiceType,
    SecretBundleType,
    SecretChangeProposalType,
    attachment_to_type,
    managed_service_to_type,
    secret_bundle_to_type,
    secret_change_proposal_to_type,
    secret_editor_from_user,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


def _secret_id(*, source: str, key: str, env: str) -> str:
    return f"{source}:{env}:{key}"


def _bundle_key_count(bundle) -> int:
    """Count of keys the bundle is expected to project (#441).

    Backed by ``SecretBundle.last_known_keys`` -- a snapshot the
    rotation activity refreshes on every materialise, plus a lazy
    on-read refresh when the cache is older than 1 h.  When the cache
    has never been populated (newly created bundle, scheduled refresh
    hasn't run yet, secrets backend unreachable) returns 0 and the UI
    shows '?' to signal "unknown" rather than "confirmed zero".
    """
    from astrolift_services.bundle_keys import known_key_count

    return known_key_count(bundle)


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

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_managed_service_objects(
        self,
        info: Info,
        managed_service_id: GUID,
        limit: int = 10,
    ) -> ManagedServiceObjectsType | None:
        """Top-N entries from the recent-objects cache for an
        `object_store` kind managed service (#401).

        Sourced from `config['recent_objects']` which the workflow loop
        is expected to refresh on a schedule.  A live cloud list call
        needs the driver SDK to expose `list_recent_objects`, filed as
        a backend gap follow-up — until then the field returns whatever
        the cache contains (often empty for freshly-provisioned rows;
        UI surfaces a friendly hint in that case).
        """
        if limit < 1:
            limit = 10
        if limit > 100:
            limit = 100

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(guid=str(managed_service_id), deleted_at__isnull=True)
            .first()
        )
        if svc is None:
            return None
        if svc.kind != ManagedService.Kind.OBJECT_STORE:
            return ManagedServiceObjectsType(
                managed_service_id=managed_service_id,
                kind=svc.kind,
                name=svc.name,
                objects=[],
                truncated=False,
                cache_age_seconds=None,
            )

        config = svc.config or {}
        raw_objects = config.get("recent_objects") or []
        sampled_at = config.get("recent_objects_sampled_at")
        cache_age = None
        if sampled_at:
            try:
                # Stored as ISO 8601 string by the workflow refresh.
                from datetime import datetime

                if isinstance(sampled_at, str):
                    parsed = datetime.fromisoformat(sampled_at.replace("Z", "+00:00"))
                else:
                    parsed = sampled_at
                from django.utils import timezone

                now = timezone.now()
                # Use timezone-aware diff; treat naive snapshots as UTC.
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=now.tzinfo)
                cache_age = max(int((now - parsed).total_seconds()), 0)
            except (ValueError, TypeError):
                cache_age = None

        objects: list[ManagedServiceObjectType] = []
        for entry in raw_objects[:limit]:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key") or entry.get("name") or ""
            if not key:
                continue
            size = entry.get("size_bytes") or entry.get("size") or 0
            last_modified_raw = entry.get("last_modified")
            last_modified = None
            if last_modified_raw:
                try:
                    from datetime import datetime

                    if isinstance(last_modified_raw, str):
                        last_modified = datetime.fromisoformat(last_modified_raw.replace("Z", "+00:00"))
                    else:
                        last_modified = last_modified_raw
                except (ValueError, TypeError):
                    last_modified = None
            objects.append(
                ManagedServiceObjectType(
                    key=str(key),
                    size_bytes=int(size) if isinstance(size, (int, float)) else 0,
                    last_modified=last_modified,
                )
            )

        return ManagedServiceObjectsType(
            managed_service_id=managed_service_id,
            kind=svc.kind,
            name=svc.name,
            objects=objects,
            truncated=len(raw_objects) > limit,
            cache_age_seconds=cache_age,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_managed_service_queue_depth(
        self,
        info: Info,
        managed_service_id: GUID,
    ) -> ManagedServiceQueueDepthType | None:
        """Cached depth snapshot for a `queue` / `topic` managed
        service (#401).

        Reads `config['depth_snapshot']` — populated by the workflow's
        scheduled refresh.  Live depth needs `queue_depth` on the driver
        SDK (backend gap follow-up); until then this field returns the
        cached values plus a `sampled_at` timestamp so operators can see
        how stale the reading is.
        """
        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(guid=str(managed_service_id), deleted_at__isnull=True)
            .first()
        )
        if svc is None:
            return None
        if svc.kind not in (ManagedService.Kind.QUEUE, ManagedService.Kind.TOPIC):
            return ManagedServiceQueueDepthType(
                managed_service_id=managed_service_id,
                kind=svc.kind,
                name=svc.name,
                depth=0,
                in_flight=0,
                sampled_at=None,
            )

        config = svc.config or {}
        snapshot = config.get("depth_snapshot") or {}
        sampled_at_raw = snapshot.get("sampled_at")
        sampled_at = None
        if sampled_at_raw:
            try:
                from datetime import datetime

                if isinstance(sampled_at_raw, str):
                    sampled_at = datetime.fromisoformat(sampled_at_raw.replace("Z", "+00:00"))
                else:
                    sampled_at = sampled_at_raw
            except (ValueError, TypeError):
                sampled_at = None

        depth = snapshot.get("depth") or 0
        in_flight = snapshot.get("in_flight") or 0
        return ManagedServiceQueueDepthType(
            managed_service_id=managed_service_id,
            kind=svc.kind,
            name=svc.name,
            depth=int(depth) if isinstance(depth, (int, float)) else 0,
            in_flight=int(in_flight) if isinstance(in_flight, (int, float)) else 0,
            sampled_at=sampled_at,
        )

    # ---- Secret-change proposals (#488) ------------------------------

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_secret_change_proposals(
        self,
        info: Info,
        app_slug: str | None = None,
        status: str | None = None,
    ) -> list[SecretChangeProposalType]:
        """List secret-change proposals for the calling tenant (#488).

        Filtered by app slug + lifecycle status when provided.  Default
        view (no filters) returns every proposal across every app the
        caller can read; the global ``/approvals`` page consumes this
        to render the mixed queue alongside deployment approvals.
        """
        qs = (
            SecretChangeProposal.objects.select_related(
                "registered_app",
                "app_environment",
                "proposer",
            )
            .filter(deleted_at__isnull=True)
            .order_by("-created_at")
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        if status:
            valid = {s.value for s in SecretChangeProposal.Status}
            if status in valid:
                qs = qs.filter(status=status)
        # Hard cap so a runaway tenant can't page-of-everything us.
        return [secret_change_proposal_to_type(p) for p in qs[:200]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_secret_change_proposal(
        self,
        info: Info,
        id: GUID,
    ) -> SecretChangeProposalType | None:
        """Single proposal detail for the proposal-detail page."""
        proposal = (
            SecretChangeProposal.objects.select_related(
                "registered_app",
                "app_environment",
                "proposer",
            )
            .filter(guid=str(id), deleted_at__isnull=True)
            .first()
        )
        if proposal is None:
            return None
        return secret_change_proposal_to_type(proposal)
