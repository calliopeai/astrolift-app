"""ManagedServiceMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    ManagedService,
)
from astrolift_services.schema.mutations.helpers import (
    _caller_org_id,
    _client_ip,
    _is_envelope_key_public,
)
from astrolift_services.schema.mutations.types import (
    DeprovisionManagedServiceInput,
    ProvisionManagedServiceInput,
    ReprovisionManagedServiceInput,
    RevealManagedServiceConnectionInput,
    UpdateManagedServiceInput,
    _ManagedServiceDeletedPayload,
)
from astrolift_services.schema.types import (
    ManagedServiceConnectionKeyType,
    ManagedServiceConnectionType,
    ManagedServiceType,
    managed_service_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ManagedServiceMutations:
    # ---- Managed services CRUD (#281) ----------------------------

    @strawberry.field
    @mutation_audit(action="managed_service.provision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def provision_managed_service(
        self,
        info: Info,
        input: ProvisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        """Provision a managed-service binding.

        DB-side write only — the actual workflow that drives the
        provider plugin's provision() lives in
        ``astrolift_workflows`` and reads from this row. The
        mutation creates the row in PENDING state; the workflow
        loop transitions it through PROVISIONING → ACTIVE."""
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        env = AppEnvironment.objects.filter(
            registered_app=app,
            name=input.environment_name,
            deleted_at__isnull=True,
        ).first()
        if env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {input.environment_name!r} not found",
                field="environmentName",
            )
        valid_kinds = {k for k, _ in ManagedService.Kind.choices}
        if input.kind not in valid_kinds:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"kind must be one of {sorted(valid_kinds)}",
                field="kind",
            )
        name = (input.name or input.kind).strip()
        if ManagedService.objects.filter(
            registered_app=app,
            kind=input.kind,
            name=name,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"managed service ({input.kind}, {name!r}) already exists for this app",
                field="name",
            )
        svc = ManagedService.objects.create(
            registered_app=app,
            app_environment=env,
            kind=input.kind,
            name=name,
            variant=input.variant or "",
            config=dict(input.config or {}),
            status=ManagedService.Status.PENDING,
        )

        # Fire the workflow that actually provisions the backend resource
        # and transitions the row PENDING -> PROVISIONING -> ACTIVE (#1001).
        # Before this, the row was created and nothing drove it — it sat
        # PENDING forever. Deterministic id de-dups re-fires via Temporal.
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            ProvisionManagedServiceInput as ProvisionInput,
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "ProvisionManagedServiceWorkflow",
            args=[
                ProvisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                ),
            ],
            workflow_id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_managed_service(
        self,
        info: Info,
        input: UpdateManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        svc = (
            ManagedService.objects.select_related(
                "app_environment__tenant_cluster__provider_plugin",
                "registered_app",
            )
            .filter(
                guid=str(input.id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
            )
        if input.config is not None:
            from astrolift_services.schema.types import _editable_fields_for

            editable = _editable_fields_for(svc)
            if editable != ["*"]:
                incoming_keys = set(dict(input.config).keys())
                current_keys = set((svc.config or {}).keys())
                changed_keys = {
                    k
                    for k in incoming_keys | current_keys
                    if dict(input.config).get(k) != (svc.config or {}).get(k)
                }
                blocked = changed_keys - set(editable)
                if blocked:
                    return gql_failure(
                        ErrorCode.VALIDATION.value,
                        f"fields {sorted(blocked)} cannot be changed in-place; "
                        "use reprovisionManagedService to apply them",
                        field="config",
                    )
        if input.name is not None:
            svc.name = input.name.strip()
        if input.config is not None:
            svc.config = dict(input.config)
        # Re-applying config kicks the workflow back to UPDATING;
        # the workflow loop will roll it forward to ACTIVE.
        if svc.status == ManagedService.Status.ACTIVE:
            svc.status = ManagedService.Status.UPDATING
        svc.save(
            update_fields=[
                "name",
                "config",
                "status",
                "updated_at",
                "version",
            ]
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.reprovision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def reprovision_managed_service(
        self,
        info: Info,
        input: ReprovisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        """Trigger a full reprovision cycle for a managed service (#745).

        Transitions status to PENDING so the lifecycle workflow picks it
        up for a fresh provision pass. Use for config changes that are
        NOT in ``editable_fields`` (i.e., changes that require tearing
        down and re-creating the backing cloud resource).
        """
        svc = (
            ManagedService.objects.select_related(
                "app_environment__tenant_cluster__provider_plugin",
                "registered_app",
            )
            .filter(
                guid=str(input.managed_service_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )
        _blocked = {
            ManagedService.Status.DEPROVISIONING,
            ManagedService.Status.PROVISIONING,
        }
        if svc.status in _blocked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"managed service is {svc.status}; reprovision can only be "
                "triggered for services that are active, pending, updating, or failed",
                field="managedServiceId",
            )
        svc.status = ManagedService.Status.PENDING
        svc.save(update_fields=["status", "updated_at", "version"])

        # Re-fire ProvisionManagedServiceWorkflow so a row that is failed/
        # pending/active actually re-runs (#1038). Before this the mutation
        # only flipped status to PENDING and returned ok=true — but nothing
        # drove the row, so it sat PENDING forever (live finding: reprovision
        # on a failed row never re-ran). Deterministic id + TERMINATE_IF_RUNNING
        # single-flights re-fires: a still-running run is superseded, a dead
        # run is replaced with a fresh one. Mirrors provision_managed_service.
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            ProvisionManagedServiceInput as ProvisionInput,
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "ProvisionManagedServiceWorkflow",
            args=[
                ProvisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                ),
            ],
            workflow_id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.deprovision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def deprovision_managed_service(
        self,
        info: Info,
        input: DeprovisionManagedServiceInput,
    ) -> MutationResultType[_ManagedServiceDeletedPayload]:
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            DeprovisionManagedServiceInput as DeprovisionInput,
        )

        svc = ManagedService.objects.filter(
            guid=str(input.id),
            registered_app__organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        ).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
            )

        # Flip to DEPROVISIONING so the UI shows the in-flight state
        # immediately. The workflow re-asserts on entry; the platform
        # row is only soft-deleted by the workflow's finalize activity
        # AFTER the driver confirms the backend resource is gone.
        svc.status = ManagedService.Status.DEPROVISIONING
        svc.save(
            update_fields=[
                "status",
                "updated_at",
                "version",
            ]
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "DeprovisionManagedServiceWorkflow",
            args=[
                DeprovisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                    delete_data=bool(input.delete_data),
                    force_destroy=bool(input.force_destroy),
                ),
            ],
            workflow_id=f"DeprovisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(
            _ManagedServiceDeletedPayload(
                id=input.id,
                deleted=False,  # workflow finalizes the soft-delete
            )
        )

    # ---- Per-service quick actions (#401) -----------------------------
    #
    # The Settings landing surfaces a managed-services summary card with
    # a per-row dropdown of kind-specific actions:
    #   - postgres / redis / mysql  → revealManagedServiceConnection
    #   - object_store              → listManagedServiceObjects (query)
    #   - email                     → sendManagedServiceTestEmail
    #   - queue / topic             → managedServiceQueueDepth (query)
    #
    # Reveal mirrors #424's pattern: explicit mutation, decorated with
    # @mutation_audit + a sibling emit_audit row for the client IP.  No
    # plaintext leaves the platform — the envelope's `value` field is
    # always a `secret-ref:` or `placeholder:` shim.

    @strawberry.field
    @mutation_audit(
        action="managed_service.connection.reveal",
        extras=lambda result: (
            {
                "managed_service_id": str(result.data.managed_service_id),
                "kind": result.data.kind,
                "environment_name": result.data.environment_name,
                "key_count": len(result.data.keys),
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="managed_service.connection.reveal")
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def reveal_managed_service_connection(
        self,
        info: Info,
        input: RevealManagedServiceConnectionInput,
    ) -> MutationResultType[ManagedServiceConnectionType]:
        """Disclose the connection envelope key set for one managed
        service (#401).

        Returns the stable env-var key set the workload sees at runtime
        for the service's kind, paired with the platform's
        `connection_secret_ref` pointer.  Plaintext values are NEVER
        returned — they live in the platform secrets backend (Vault /
        SecretsManager / GSM / KeyVault) and aren't reachable from this
        API surface.  Each `value` field is the opaque
        ``secret-ref:<ref>`` or ``placeholder:<note>`` shim that points
        the operator at where to fetch the value via the platform's
        secrets-backend client.

        Permission gate stacks `app.read` (caller can see the app) with
        `managed_service.update` (caller can disclose the pointer) so
        the surface matches the rest of the per-service action set.
        """
        # Lazy import to avoid circulars; the env_injection module is
        # the source of truth for the envelope key set per kind.
        from astrolift_manifest.env_injection import envelope_keys_for

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(
                guid=str(input.managed_service_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )

        envelope = envelope_keys_for(svc.kind)
        if not envelope:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"kind {svc.kind!r} has no connection envelope to reveal; "
                    "reveal is only meaningful for kinds the platform injects "
                    "env vars for (postgres, redis, mysql, queue, topic, "
                    "object_store, email, etc.)"
                ),
                field="managedServiceId",
            )

        # Shape each key as ``secret-ref:<ref>`` when the workflow has
        # populated `connection_secret_ref`, else ``placeholder:pending``
        # so the UI can render a clear "not yet provisioned" hint
        # without us inventing a fake value.
        ref = svc.connection_secret_ref or ""
        if ref:
            value_for = lambda k: f"secret-ref:{ref}#{k}"  # noqa: E731
        else:
            value_for = lambda k: "placeholder:pending"  # noqa: E731

        keys = [
            ManagedServiceConnectionKeyType(
                key=k,
                value=value_for(k),
                # The handful of non-secret envelope keys are stable
                # config (region, prefix) — surface them as is_secret=
                # False so the UI doesn't mask them.
                is_secret=not _is_envelope_key_public(k),
            )
            for k in envelope
        ]

        # Sibling audit row carrying the client IP so the trail captures
        # the disclosure source (#424 pattern).  Done as a separate
        # emit_audit call so the IP isn't surfaced in the GraphQL
        # response (which would leak the caller's IP to a layered
        # proxy).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.connection.reveal.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_READ.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "kind": svc.kind,
                    "name": svc.name,
                    "app_slug": svc.registered_app.slug,
                    "environment_name": svc.app_environment.name,
                    "key_count": len(keys),
                    "connection_secret_ref": ref,
                    "client_ip": ip,
                },
            )
        )

        # Stamp the cached "last operator action" surface so the
        # summary card can render "revealed N seconds ago" without
        # re-walking the audit log.
        now = timezone.now()
        svc.last_action_at = now
        svc.last_action_kind = "connection.reveal"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        return gql_success(
            ManagedServiceConnectionType(
                managed_service_id=input.managed_service_id,
                kind=svc.kind,
                name=svc.name,
                environment_name=svc.app_environment.name,
                connection_secret_ref=ref,
                keys=keys,
                revealed_at=now,
            )
        )
