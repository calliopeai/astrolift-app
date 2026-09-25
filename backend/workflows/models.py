"""Workflow Engine models.

WorkflowDefinition: DB-configurable state machine (states, transitions, conditions, actions).
WorkflowInstance: tracks a specific object through a workflow.
TransitionLog: immutable history of every state change.
"""

import logging

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import BaseCoreModel, Tracking

logger = logging.getLogger(__name__)


class WorkflowDefinition(BaseCoreModel):
    """A reusable workflow definition with states and transitions.

    States and transitions are stored as JSON, making workflows
    configurable without Python code.

    State format:
        [{"name": "draft", "label": "Draft", "is_initial": true, "is_final": false, "color": "#6b7280"}]

    Transition format:
        [{"from_state": "draft", "to_state": "submitted", "label": "Submit",
          "conditions": [{"type": "user_has_role", "role": "submitter"}],
          "actions": [{"type": "notify_user", "user": "form_owner"}],
          "timeout_hours": null}]
    """

    class PatternKind(models.TextChoices):
        # One agent, one task — synchronous or async.
        SINGLE = "single"
        # Tasks run in sequence; each stage receives the prior stage's output.
        CHAINED = "chained"
        # One trigger spawns N parallel tasks; results are aggregated before
        # the next stage begins.
        FAN_OUT = "fan_out"
        # Supervisor agent routes sub-tasks to worker agents dynamically.
        SUPERVISOR_WORKER = "supervisor_worker"
        # Agent produces output → human gate approves/rejects → agent iterates.
        REVIEW_LOOP = "review_loop"
        # Agent runs in read-only observe mode alongside another task.
        ADVISOR = "advisor"

    pattern_kind = models.CharField(
        max_length=32,
        choices=PatternKind.choices,
        default=PatternKind.SINGLE,
        db_index=True,
        help_text="Multi-agent composition pattern for this workflow definition.",
    )

    model_label = models.CharField(
        max_length=100,
        help_text=(
            'Legacy state-machine target (for example "forms.FormSubmission"). '
            "Agent pipelines leave this blank; it is not an LLM model selector."
        ),
        db_index=True,
    )
    states = models.JSONField(
        default=list,
        help_text="List of state definitions [{name, label, is_initial, is_final, color}]",
    )
    transitions = models.JSONField(
        default=list,
        help_text="List of transition definitions [{from_state, to_state, label, conditions, actions, timeout_hours}]",
    )
    is_enabled = models.BooleanField(default=True)
    source_repo = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Source repository that declaratively owns this definition.",
    )
    source_path = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Repository-relative workflow manifest path.",
    )
    source_ref = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Last repository ref reconciled into this definition.",
    )

    # Tier-1 org scope (spec 40 §2.1). NULL → platform-global template,
    # read-only to tenants (write mutations reject; superuser/staff may seed).
    # Set → org-authored, editable by that org.
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="workflow_definitions",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="workflow_definitions",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Project packet that owns this repository workflow; null for reusable templates.",
    )
    # Drop the global field-level slug uniqueness inherited from the legacy
    # BaseCoreModel: a slug is unique *per org* now (and a platform-global may
    # share a slug with an org's clone), enforced by (organization, slug).
    slug = models.SlugField(max_length=100, null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            # Live rows only — a soft-deleted definition must not squat its
            # slug (same partial-uniqueness convention as NamedBaseCoreModel).
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="workflowdefinition_org_slug_unique",
            ),
            models.UniqueConstraint(
                fields=["organization", "source_repo", "source_path"],
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(source_repo=""),
                name="workflowdefinition_org_source_unique",
            ),
        ]

    @classmethod
    def visible_to_org(cls, organization_id):
        """Read scope (spec 40 §2.1): the org's own definitions UNION all
        platform-global (null-org) definitions."""
        from django.db.models import Q

        return cls.objects.filter(Q(organization_id=organization_id) | Q(organization__isnull=True))

    def get_initial_state(self) -> str | None:
        for state in self.states:
            if state.get("is_initial"):
                return state["name"]
        return self.states[0]["name"] if self.states else None

    def get_final_states(self) -> set[str]:
        return {s["name"] for s in self.states if s.get("is_final")}

    def get_state_label(self, state_name: str) -> str:
        for s in self.states:
            if s["name"] == state_name:
                return s.get("label", state_name)
        return state_name

    def get_available_transitions(self, from_state: str) -> list[dict]:
        return [t for t in self.transitions if t["from_state"] == from_state]

    def get_transition(self, from_state: str, to_state: str) -> dict | None:
        for t in self.transitions:
            if t["from_state"] == from_state and t["to_state"] == to_state:
                return t
        return None

    def validate_definition(self) -> list[str]:
        """Validate the workflow definition for correctness."""
        errors = []
        state_names = {s["name"] for s in self.states}
        initial_count = sum(1 for s in self.states if s.get("is_initial"))

        if not self.states:
            errors.append("Workflow must have at least one state")
        if initial_count != 1:
            errors.append(f"Workflow must have exactly one initial state (found {initial_count})")

        for t in self.transitions:
            if t["from_state"] not in state_names:
                errors.append(f"Transition from unknown state: {t['from_state']}")
            if t["to_state"] not in state_names:
                errors.append(f"Transition to unknown state: {t['to_state']}")

        return errors

    def __str__(self):
        return f"{self.name} ({self.model_label})"


class WorkflowInstance(Tracking):
    """Tracks a specific object through a workflow.

    Uses GenericForeignKey to attach to any Django model.
    """

    # Legacy tier (forms state-machine): now nullable — agent runs use
    # ``configured_workflow`` instead and stop faking the GFK (spec 40 §2.3).
    workflow = models.ForeignKey(
        WorkflowDefinition,
        on_delete=models.PROTECT,
        related_name="instances",
        null=True,
        blank=True,
    )
    # Tier-2 link: the configured Workflow this run belongs to (spec 40 §2.3).
    configured_workflow = models.ForeignKey(
        "Workflow",
        on_delete=models.PROTECT,
        related_name="runs",
        null=True,
        blank=True,
    )
    # Denormalized from the Workflow on start() so the tenancy guardrail +
    # indexes work without a 2-level join (spec 40 §2.3).
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        on_delete=models.CASCADE,
        related_name="workflow_instances",
        null=True,
        blank=True,
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")

    current_state = models.CharField(max_length=100, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Temporal integration (future)
    temporal_workflow_id = models.CharField(max_length=200, null=True, blank=True)
    # Temporal run_id captured at start (#1180). A historical run's stage
    # executions can then overlay the run DAG without a live Temporal describe,
    # which returns null once Temporal GCs the run's history.
    temporal_run_id = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["workflow", "current_state"]),
            models.Index(fields=["organization", "current_state"]),
        ]

    @classmethod
    def start(
        cls,
        workflow: WorkflowDefinition | None = None,
        obj=None,
        user=None,
        *,
        configured_workflow: "Workflow | None" = None,
        temporal_workflow_id: str | None = None,
        temporal_run_id: str | None = None,
        organization_id: int | None = None,
    ) -> "WorkflowInstance":
        """Start a new workflow instance.

        ``configured_workflow``-first path (spec 40 §2.3): agent runs start
        from a tier-2 Workflow; the org is denormalized off it and the legacy
        GFK is left null. The legacy forms state-machine path (positional
        ``workflow`` + ``obj``) records ``organization_id`` when the caller
        passes one: tenants read and transition only their own org's
        instances (#1965).
        """
        if configured_workflow is not None:
            instance = cls.objects.create(
                configured_workflow=configured_workflow,
                organization_id=configured_workflow.organization_id,
                # Keep the legacy mirror FK pointed at the shape for back-compat
                # (UI reads instance.workflow.name / .slug).
                workflow=configured_workflow.definition,
                current_state="running",
                temporal_workflow_id=temporal_workflow_id,
                temporal_run_id=temporal_run_id,
                created_by=user,
                updated_by=user,
            )
            # A short workflow may settle before this configured-run row is
            # created. Catch that race using the exact execution mirror.
            from astrolift_operations.models import WorkflowRun
            from workflows.run_status import synchronize_workflow_instances

            run = WorkflowRun.objects.filter(
                organization_id=instance.organization_id,
                workflow_id=temporal_workflow_id,
                run_id=temporal_run_id or "",
            ).first()
            if run is not None:
                synchronize_workflow_instances(run)
                instance.refresh_from_db()
            return instance

        initial_state = workflow.get_initial_state()
        if not initial_state:
            raise ValidationError("Workflow has no initial state")

        ct = ContentType.objects.get_for_model(obj)
        instance = cls.objects.create(
            workflow=workflow,
            organization_id=organization_id,
            content_type=ct,
            object_id=obj.pk,
            current_state=initial_state,
            created_by=user,
            updated_by=user,
        )

        TransitionLog.objects.create(
            instance=instance,
            from_state="",
            to_state=initial_state,
            transitioned_by=user,
            note="Workflow started",
        )

        return instance

    def transition(self, to_state: str, user=None, note: str = "") -> "TransitionLog":
        """Execute a state transition.

        Validates the transition exists and evaluates conditions.
        Fires actions after successful transition.
        """
        transition_def = self.workflow.get_transition(self.current_state, to_state)
        if not transition_def:
            raise ValidationError(
                f'No transition from "{self.current_state}" to "{to_state}" '
                f'in workflow "{self.workflow.name}"'
            )

        # Evaluate conditions
        conditions = transition_def.get("conditions", [])
        for condition in conditions:
            if not self._evaluate_condition(condition, user):
                raise ValidationError(f"Condition not met: {condition.get('type', 'unknown')}")

        # Execute transition
        from_state = self.current_state
        self.current_state = to_state
        self.updated_by = user

        # Check if final state
        if to_state in self.workflow.get_final_states():
            self.completed_at = timezone.now()

        self.save()

        # Log
        log = TransitionLog.objects.create(
            instance=self,
            from_state=from_state,
            to_state=to_state,
            transitioned_by=user,
            note=note or transition_def.get("label", ""),
        )

        # Fire actions asynchronously
        actions = transition_def.get("actions", [])
        for action in actions:
            self._fire_action(action, from_state, to_state, user)

        return log

    def get_available_transitions(self, user=None) -> list[dict]:
        """Get transitions available from the current state."""
        transitions = self.workflow.get_available_transitions(self.current_state)
        available = []
        for t in transitions:
            # Check conditions
            conditions_met = all(self._evaluate_condition(c, user) for c in t.get("conditions", []))
            available.append(
                {
                    **t,
                    "conditions_met": conditions_met,
                }
            )
        return available

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None

    def _evaluate_condition(self, condition: dict, user) -> bool:
        """Evaluate a transition condition."""
        ctype = condition.get("type", "")

        if ctype == "user_has_role":
            if not user:
                return False
            role = condition.get("role", "")
            return user.groups.filter(name=role).exists() or user.is_superuser

        if ctype == "field_equals":
            obj = self.content_object
            field = condition.get("field", "")
            value = condition.get("value")
            return getattr(obj, field, None) == value

        if ctype == "field_in":
            obj = self.content_object
            field = condition.get("field", "")
            values = condition.get("values", [])
            return getattr(obj, field, None) in values

        if ctype == "is_authenticated":
            return user and user.is_authenticated

        if ctype == "is_superuser":
            return user and user.is_superuser

        # Unknown condition type — pass by default
        logger.warning(f"Unknown condition type: {ctype}")
        return True

    def _fire_action(self, action: dict, from_state: str, to_state: str, user):
        """Fire a transition action.

        Runs the action handler directly. For long-running actions,
        dispatch as a Temporal activity instead.
        """
        try:
            from workflows.tasks import execute_workflow_action

            execute_workflow_action(
                instance_id=self.pk,
                action=action,
                from_state=from_state,
                to_state=to_state,
                user_id=user.pk if user else None,
            )
        except Exception as e:
            logger.warning(f"Failed to fire action {action}: {e}")

    def __str__(self):
        return f"{self.workflow.name}: {self.current_state} (obj={self.object_id})"


class TransitionLog(models.Model):
    """Immutable log of every state transition."""

    instance = models.ForeignKey(
        WorkflowInstance,
        on_delete=models.CASCADE,
        related_name="transition_logs",
    )
    from_state = models.CharField(max_length=100)
    to_state = models.CharField(max_length=100)
    transitioned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    note = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.from_state} → {self.to_state} at {self.timestamp}"


class WorkflowStage(BaseCoreModel):
    """One ordered stage within an agent WorkflowDefinition.

    A stage declares what happens at position ``order`` in the sequence —
    dispatching an agent, waiting on a human gate, aggregating fan-out
    results, or capturing a checkpoint. Stages are the static definition;
    per-run execution records live on WorkflowStageExecution.
    """

    class StageKind(models.TextChoices):
        # Dispatches an agent workload (requires agent_definition).
        AGENT_DISPATCH = "agent_dispatch"
        # Pause for human approval / rejection before proceeding.
        HUMAN_GATE = "human_gate"
        # Intermediate checkpoint — snapshot state, no external dispatch.
        CHECKPOINT = "checkpoint"
        # Collect and merge outputs from a preceding fan_out stage.
        AGGREGATION = "aggregation"
        # Execute another WorkflowDefinition as a linked Temporal child run.
        WORKFLOW = "workflow"

    class OnFailure(models.TextChoices):
        FAIL = "fail"
        RETRY = "retry"
        SKIP = "skip"
        ESCALATE = "escalate"

    # Slug is identified by (definition, order); drop the legacy global
    # field-level uniqueness so an org clone + the global it came from can
    # both carry a "<slug>-stage-0" stage slug.
    slug = models.SlugField(max_length=100, null=True, blank=True, db_index=True)

    definition = models.ForeignKey(
        WorkflowDefinition,
        related_name="stages",
        on_delete=models.CASCADE,
    )
    order = models.IntegerField(
        help_text="0-based position of this stage within the workflow sequence.",
    )
    kind = models.CharField(
        max_length=32,
        choices=StageKind.choices,
        default=StageKind.AGENT_DISPATCH,
    )
    # Nullable — only AGENT_DISPATCH stages reference a Workload. The FK is a
    # soft guard; callers should validate kind == "agent" on the Workload.
    agent_definition = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="workflow_stages",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Agent workload to dispatch at this stage (kind=agent_dispatch only).",
    )
    # Stable manifest reference.  The FK above is an optional eager binding;
    # this slug survives import before the referenced agent is registered and
    # is resolved inside the run's organization when the workflow starts.
    agent_ref = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Organization-local agent workload slug used by the workflow manifest.",
    )
    workflow_ref = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Visible child WorkflowDefinition slug used by kind=workflow stages.",
    )
    environment_spec_slug = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Default org-scoped AgentEnvironmentSpec slug for this stage.",
    )
    # Ordered list of Skill slugs injected into the agent at dispatch time.
    skill_refs = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of Skill slugs injected at dispatch.",
    )
    # Fan-out as a clean tri-state (spec 40 §5.4 manifest ``fan_out``
    # "0"/N/"dynamic"):
    #   none    → fan_out_count is NULL and fan_out_dynamic is False
    #   static  → fan_out_count = N (>0) and fan_out_dynamic is False
    #   dynamic → fan_out_count is NULL and fan_out_dynamic is True
    # (derive the spawn count from the prior stage's output list at run time).
    fan_out_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Static parallel-task count for fan_out stages. Null → none (or dynamic, see fan_out_dynamic).",
    )
    fan_out_dynamic = models.BooleanField(
        default=False,
        help_text="When true, spawn count is derived dynamically from the prior stage's output.",
    )
    on_failure = models.CharField(
        max_length=16,
        choices=OnFailure.choices,
        default=OnFailure.FAIL,
        help_text="What to do if this stage fails.",
    )
    timeout_seconds = models.IntegerField(
        default=300,
        help_text="Maximum wall-clock time for this stage before it times out.",
    )
    # Human-readable label for the agent that belongs at this stage; used by
    # the builder/binding UI when ``agent_definition`` is null (globals).
    # Optional for org definitions. (spec 40 §2.4)
    role = models.CharField(max_length=64, blank=True, default="")
    # For agent stages this is an immutable per-dispatch instruction overlay;
    # for human gates it is the question shown to approvers.
    prompt = models.TextField(
        blank=True,
        default="",
        help_text="Agent stage instruction overlay or human-gate approval prompt.",
    )
    output_key = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Name under which this stage's structured result is exposed to later stages.",
    )
    approvers = models.JSONField(
        default=list,
        blank=True,
        help_text="Approver references for human_gate/escalation stages (e.g. team or role slugs).",
    )

    class Meta:
        ordering = ["definition", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["definition", "order"],
                name="workflowstage_definition_order_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["definition", "order"], name="wfstage_def_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.definition.name} stage {self.order} ({self.kind})"


class Workflow(BaseCoreModel):
    """Tier-2 configured, runnable workflow (spec 40 §2.2).

    A tenant's named application of a ``WorkflowDefinition`` to a concrete
    config: per-stage agent bindings + default inputs + a trigger. This is
    the entity the Workflows module entitlement gates (``me.modules.workflows``).
    """

    class TriggerKind(models.TextChoices):
        MANUAL = "manual"
        SCHEDULE = "schedule"
        EVENT = "event"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="workflows",
        on_delete=models.CASCADE,
    )
    definition = models.ForeignKey(
        WorkflowDefinition,
        related_name="workflows",
        on_delete=models.PROTECT,
    )
    # Per-stage concrete bindings keyed by stage order:
    # {stage_order: {"agent_workload_id": guid, "skill_refs": [...], "params": {...}}}
    stage_bindings = models.JSONField(default=dict, blank=True)
    # Workflow-level default inputs, merged with per-trigger inputs at run time.
    inputs = models.JSONField(default=dict, blank=True)
    trigger_kind = models.CharField(
        max_length=16,
        choices=TriggerKind.choices,
        default=TriggerKind.MANUAL,
    )
    schedule_cron = models.CharField(max_length=128, null=True, blank=True)
    trigger_ref = models.CharField(max_length=200, null=True, blank=True)
    is_enabled = models.BooleanField(default=True)

    # Slug is unique per org (not globally) — drop the legacy field-level
    # uniqueness inherited from BaseCoreModel.
    slug = models.SlugField(max_length=100, null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            # Live rows only — soft delete must free the slug (see
            # workflowdefinition_org_slug_unique).
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="workflow_org_slug_unique",
            ),
        ]

    def unbound_agent_stages(self) -> list[tuple[int, str]]:
        """Return ``[(order, role)]`` for every ``agent_dispatch`` stage that
        resolves to no concrete agent — neither a
        ``stage_bindings[order].agent_workload_id`` nor the stage's own
        ``agent_definition`` (spec 40 §2.2)."""
        unbound: list[tuple[int, str]] = []
        bindings = self.stage_bindings or {}
        stages = self.definition.stages.filter(
            deleted_at__isnull=True,
            kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        ).order_by("order")
        for stage in stages:
            binding = bindings.get(str(stage.order))
            if binding is None:
                binding = bindings.get(stage.order)
            bound = bool(binding and binding.get("agent_workload_id"))
            if not bound and stage.agent_definition_id is None and stage.agent_ref:
                from astrolift_registry.models import Workload

                bound = Workload.objects.filter(
                    registered_app__organization_id=self.organization_id,
                    slug=stage.agent_ref,
                    kind=Workload.Kind.AGENT,
                    deleted_at__isnull=True,
                ).exists()
            if not bound and stage.agent_definition_id is None:
                unbound.append((stage.order, stage.role or ""))
        return unbound

    def invalid_binding_shapes(self) -> list[str]:
        """Return deterministic validation errors for malformed stage bindings."""
        live_orders = set(
            self.definition.stages.filter(deleted_at__isnull=True).values_list("order", flat=True)
        )
        errors: list[str] = []
        for raw_order, binding in (self.stage_bindings or {}).items():
            try:
                order = int(raw_order)
            except (TypeError, ValueError):
                errors.append(f"binding key {raw_order!r} is not a stage order")
                continue
            if order not in live_orders:
                errors.append(f"stage {order} does not exist")
            if not isinstance(binding, dict):
                errors.append(f"stage {order} binding must be an object")
                continue
            if "skill_refs" in binding and (
                not isinstance(binding["skill_refs"], list)
                or any(not isinstance(ref, str) or not ref.strip() for ref in binding["skill_refs"])
            ):
                errors.append(f"stage {order} skill_refs must be a list of non-empty strings")
            params = binding.get("params", {})
            if not isinstance(params, dict):
                errors.append(f"stage {order} params must be an object")
                continue
            for field in ("environment_spec_slug", "prompt", "output_key"):
                if field in params and not isinstance(params[field], str):
                    errors.append(f"stage {order} params.{field} must be a string")
        return errors

    def unresolved_agent_bindings(self) -> list[str]:
        """Return the stage-order keys of ``stage_bindings`` entries whose
        ``agent_workload_id`` does not resolve to a live ``kind=agent``
        Workload in this workflow's org (#1094).

        Same org scoping ``createWorkflowStage`` uses — the Workload's org
        lives via ``registered_app.organization``, so a foreign org's
        workload (or a garbage / deleted / non-agent guid) is simply
        unresolved, never a cross-tenant bind."""
        from astrolift_registry.models import Workload

        unresolved: list[str] = []
        for order, binding in (self.stage_bindings or {}).items():
            workload_id = binding.get("agent_workload_id") if isinstance(binding, dict) else None
            if not workload_id:
                continue
            try:
                resolved = Workload.objects.filter(
                    guid=str(workload_id),
                    kind=Workload.Kind.AGENT,
                    registered_app__organization_id=self.organization_id,
                ).exists()
            except (ValueError, ValidationError):  # not even a valid guid
                resolved = False
            if not resolved:
                unresolved.append(str(order))
        return unresolved

    def validate_bindings(self) -> None:
        """Raise ``ValidationError`` naming unbound ``agent_dispatch`` stages
        (spec 40 §2.2: validation on save/run)."""
        malformed = self.invalid_binding_shapes()
        if malformed:
            raise ValidationError("Invalid stage_bindings: " + "; ".join(malformed))
        unbound = self.unbound_agent_stages()
        if unbound:
            labels = ", ".join(f"stage {order}" + (f" ({role})" if role else "") for order, role in unbound)
            raise ValidationError(f"Unbound agent_dispatch stage(s): {labels}")
        unresolved = self.unresolved_agent_bindings()
        if unresolved:
            labels = ", ".join(f"stage {order}" for order in unresolved)
            raise ValidationError(
                "stage_bindings agent_workload_id does not resolve to a live "
                f"kind=agent workload in this organization: {labels}"
            )

    def save(self, *args, **kwargs):
        skip_binding_validation = kwargs.pop("skip_binding_validation", False)
        if self.organization_id is None:
            raise ValidationError("Workflow requires an organization")
        if not skip_binding_validation and self.definition_id is not None:
            self.validate_bindings()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} (org={self.organization_id})"


class WorkflowStageExecution(BaseCoreModel):
    """Per-run execution record for one WorkflowStage.

    Created by the Temporal worker when a stage begins; status is
    updated via the Controller API (workers do not write DB directly).

    One WorkflowStageExecution per spawned task for FAN_OUT stages — the
    aggregation stage references all fan-out executions that fed it via
    the ``fan_out_sources`` reverse relation.

    Append-only after reaching a terminal status: COMPLETED / FAILED /
    SKIPPED / ESCALATED. Enforcement is handled at the application layer
    (and optionally a DB trigger added in a follow-up migration).
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        SKIPPED = "skipped"
        ESCALATED = "escalated"
        CANCELLED = "cancelled"

    TERMINAL_STATUSES = {
        Status.COMPLETED,
        Status.FAILED,
        Status.SKIPPED,
        Status.ESCALATED,
        Status.CANCELLED,
    }

    workflow_run = models.ForeignKey(
        "astrolift_operations.WorkflowRun",
        related_name="stage_executions",
        on_delete=models.CASCADE,
    )
    stage = models.ForeignKey(
        WorkflowStage,
        related_name="executions",
        on_delete=models.PROTECT,
    )
    # Set when the stage dispatches an agent run. Null for HUMAN_GATE /
    # CHECKPOINT stages which have no associated agent dispatch.
    agent_run = models.ForeignKey(
        "astrolift_lifecycle.AgentRun",
        related_name="stage_executions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # For fan_out aggregation stages: which fan-out executions fed this one.
    fan_out_sources = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="aggregated_by",
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempt_number = models.IntegerField(
        default=1,
        help_text="1-based retry count — incremented each time the stage is retried.",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    # Stage output for chaining into the next stage.
    output = models.JSONField(
        null=True,
        blank=True,
        help_text="Stage result payload passed as input to the next stage.",
    )
    failure = models.JSONField(
        null=True,
        blank=True,
        help_text="Structured failure details (error kind, message, stack excerpt).",
    )
    error_message = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["workflow_run", "stage"],
                name="wfstageexec_run_stage_idx",
            ),
            models.Index(
                fields=["workflow_run", "-started_at"],
                name="wfstageexec_run_started_idx",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def __str__(self) -> str:
        return f"StageExecution {self.stage} run={self.workflow_run_id} [{self.status}]"
