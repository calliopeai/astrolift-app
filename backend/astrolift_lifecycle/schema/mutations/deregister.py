"""DeregisterMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.schema.mutations.types import (
    CancelDeregisterInput,
    CancelDeregisterPayload,
    DeregisterAppInput,
    DeregisterAppPayload,
)
from astrolift_lifecycle.scopes import deregister_workflow_scope
from astrolift_lifecycle.visibility import live_app_rows
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from astrolift_workflows.client import (
    signal_workflow,
    start_workflow,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class DeregisterMutations:
    # ----------------------------------------------------------------
    # Danger-zone hard deregister (#392)
    # ----------------------------------------------------------------
    #
    # Fires ``DeregisterAppWorkflow`` (#392) with a deterministic
    # workflow id (``DeregisterAppWorkflow-<app-guid>``) so re-firing
    # the mutation joins the existing run rather than starting a
    # parallel teardown — partial failures are resumable. The
    # ``confirm_name`` field is a muscle-memory guard: the operator
    # must type the app's name verbatim to enable the destructive
    # click in the FE modal.

    @strawberry.field
    @mutation_audit(action="app.deregister")
    @requires_elevation(action_label="app.deregister")
    @require_permission(
        Permission.APP_DELETE, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_DELETE)
    )
    @tenant_scoped()
    def deregister_astrolift_app(
        self,
        info: Info,
        input: DeregisterAppInput,
    ) -> MutationResultType[DeregisterAppPayload]:
        """Hard-deregister an app and tear down every per-app cloud
        resource (#392).

        Validates the typed-confirmation guard (``confirm_name`` must
        equal the app's ``name``) before kicking the workflow off.
        Returns the workflow id immediately; the workflow does the
        per-resource teardown async and reports each step's outcome
        on its result envelope.

        Re-firing the same mutation joins the existing workflow run
        via Temporal de-dup on the workflow id — operators retry on
        partial failure by clicking Deregister again."""
        # NOTE: ``start_workflow`` is imported at module scope so the
        # ``temporal_recorder`` fixture's monkeypatch on
        # ``astrolift_lifecycle.schema.mutations.start_workflow``
        # actually intercepts the call. Local re-imports would
        # silently bypass the recorder.
        from astrolift_workflows.inputs import (
            Actor as _Actor,
        )
        from astrolift_workflows.inputs import (
            DeregisterAppInput as _DeregisterInput,
        )

        slug = (input.app_slug or "").strip()
        if not slug:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "app_slug is required",
                field="appSlug",
            )

        # Org-scope the app lookup to the caller's tenant BEFORE kicking the
        # deregister workflow — this is the most destructive op on the
        # platform (tears down every per-app cloud resource). Slugs are
        # unique only within an org. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {slug!r} not found",
                field="appSlug",
            )

        confirm = (input.confirm_name or "").strip()
        if confirm != app.name:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "confirm_name must match the app's name exactly",
                field="confirmName",
            )

        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        actor = _Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(
                getattr(user, "email", "") or getattr(user, "username", ""),
            ),
        )

        workflow_id = f"DeregisterAppWorkflow-{app.guid}"
        # Deterministic workflow id makes re-firing the mutation
        # converge on the existing run; Temporal de-dups by id.
        start_workflow(
            "DeregisterAppWorkflow",
            args=[
                _DeregisterInput(
                    registered_app_id=app.pk,
                    actor=actor,
                    # Operator opt-in (#1000); defaults keep + snapshot.
                    delete_data=bool(input.delete_data),
                    force_destroy=bool(input.force_destroy),
                ),
            ],
            workflow_id=workflow_id,
        )

        return gql_success(
            DeregisterAppPayload(
                workflow_id=workflow_id,
                still_live_resources=[],
            ),
        )

    # ----------------------------------------------------------------
    # Grace-period teardown cancel (#436 B)
    # ----------------------------------------------------------------
    #
    # Sends the ``cancel_teardown`` signal to a running
    # ``DeregisterAppWorkflow``. If the signal lands within the 5-min
    # grace window before the workflow's first destructive activity
    # fires, the teardown short-circuits and no per-app resource is
    # touched. After the window elapses the signal is a no-op — the
    # destructive activities are monotonic by design (a half-applied
    # teardown is not rolled back; the operator retries instead).
    # Gated on ``app.delete`` — same permission the deregister
    # mutation requires.

    @strawberry.field
    @mutation_audit(action="app.deregister.cancel")
    @require_permission(Permission.APP_DELETE, scope=deregister_workflow_scope)
    @tenant_scoped()
    def cancel_astrolift_deregister(
        self,
        info: Info,
        input: CancelDeregisterInput,
    ) -> MutationResultType[CancelDeregisterPayload]:
        """Cancel a pending deregister within the 5-min grace window."""
        wf_id = (input.workflow_id or "").strip()
        if not wf_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "workflow_id is required",
                field="workflowId",
            )
        # Defence-in-depth: the deterministic deregister workflow id
        # carries the app guid; pin the id shape here so an operator
        # can't accidentally cancel an unrelated workflow.
        if not wf_id.startswith("DeregisterAppWorkflow-"):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "workflow_id must reference a DeregisterAppWorkflow run",
                field="workflowId",
            )
        # Resolve + org-scope the app guid embedded in the workflow id.
        # ``@tenant_scoped`` only asserts a tenant exists — it does NOT
        # filter — so the explicit ``organization_id`` clause below is what
        # makes a sibling-org guid short-circuit to NOT_FOUND rather than
        # signalling another org's teardown workflow. Fails closed when
        # org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app_guid = wf_id[len("DeregisterAppWorkflow-") :]
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(guid=app_guid, organization_id=org_id)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"no deregister workflow found for {wf_id!r}",
                field="workflowId",
            )

        reason = (input.reason or "").strip()
        delivered = signal_workflow(wf_id, "cancel_teardown", reason)
        # Best-effort: signal failure (workflow not running, Temporal
        # disabled in dev, etc.) is surfaced as ok=True with
        # ``signal_delivered=False`` so the FE can render the
        # "couldn't reach" copy instead of a generic error toast.
        return gql_success(
            CancelDeregisterPayload(
                workflow_id=wf_id,
                signal_delivered=bool(delivered),
            ),
        )
