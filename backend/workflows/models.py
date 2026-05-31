"""Workflow Engine models.

WorkflowDefinition: DB-configurable state machine (states, transitions, conditions, actions).
WorkflowInstance: tracks a specific object through a workflow.
TransitionLog: immutable history of every state change.
"""
import logging
from typing import Optional

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
        help_text='Django model this workflow applies to (e.g. "forms.FormSubmission")',
        db_index=True,
    )
    states = models.JSONField(
        default=list,
        help_text='List of state definitions [{name, label, is_initial, is_final, color}]',
    )
    transitions = models.JSONField(
        default=list,
        help_text='List of transition definitions [{from_state, to_state, label, conditions, actions, timeout_hours}]',
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['slug', 'model_label'], name='unique_workflow_per_model'),
        ]

    def get_initial_state(self) -> Optional[str]:
        for state in self.states:
            if state.get('is_initial'):
                return state['name']
        return self.states[0]['name'] if self.states else None

    def get_final_states(self) -> set[str]:
        return {s['name'] for s in self.states if s.get('is_final')}

    def get_state_label(self, state_name: str) -> str:
        for s in self.states:
            if s['name'] == state_name:
                return s.get('label', state_name)
        return state_name

    def get_available_transitions(self, from_state: str) -> list[dict]:
        return [t for t in self.transitions if t['from_state'] == from_state]

    def get_transition(self, from_state: str, to_state: str) -> Optional[dict]:
        for t in self.transitions:
            if t['from_state'] == from_state and t['to_state'] == to_state:
                return t
        return None

    def validate_definition(self) -> list[str]:
        """Validate the workflow definition for correctness."""
        errors = []
        state_names = {s['name'] for s in self.states}
        initial_count = sum(1 for s in self.states if s.get('is_initial'))

        if not self.states:
            errors.append('Workflow must have at least one state')
        if initial_count != 1:
            errors.append(f'Workflow must have exactly one initial state (found {initial_count})')

        for t in self.transitions:
            if t['from_state'] not in state_names:
                errors.append(f'Transition from unknown state: {t["from_state"]}')
            if t['to_state'] not in state_names:
                errors.append(f'Transition to unknown state: {t["to_state"]}')

        return errors

    def __str__(self):
        return f'{self.name} ({self.model_label})'


class WorkflowInstance(Tracking):
    """Tracks a specific object through a workflow.

    Uses GenericForeignKey to attach to any Django model.
    """
    workflow = models.ForeignKey(
        WorkflowDefinition,
        on_delete=models.PROTECT,
        related_name='instances',
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    current_state = models.CharField(max_length=100, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Temporal integration (future)
    temporal_workflow_id = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['workflow', 'current_state']),
        ]

    @classmethod
    def start(cls, workflow: WorkflowDefinition, obj, user=None) -> 'WorkflowInstance':
        """Start a new workflow instance for an object."""
        initial_state = workflow.get_initial_state()
        if not initial_state:
            raise ValidationError('Workflow has no initial state')

        ct = ContentType.objects.get_for_model(obj)
        instance = cls.objects.create(
            workflow=workflow,
            content_type=ct,
            object_id=obj.pk,
            current_state=initial_state,
            created_by=user,
            updated_by=user,
        )

        TransitionLog.objects.create(
            instance=instance,
            from_state='',
            to_state=initial_state,
            transitioned_by=user,
            note='Workflow started',
        )

        return instance

    def transition(self, to_state: str, user=None, note: str = '') -> 'TransitionLog':
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
        conditions = transition_def.get('conditions', [])
        for condition in conditions:
            if not self._evaluate_condition(condition, user):
                raise ValidationError(
                    f'Condition not met: {condition.get("type", "unknown")}'
                )

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
            note=note or transition_def.get('label', ''),
        )

        # Fire actions asynchronously
        actions = transition_def.get('actions', [])
        for action in actions:
            self._fire_action(action, from_state, to_state, user)

        return log

    def get_available_transitions(self, user=None) -> list[dict]:
        """Get transitions available from the current state."""
        transitions = self.workflow.get_available_transitions(self.current_state)
        available = []
        for t in transitions:
            # Check conditions
            conditions_met = all(
                self._evaluate_condition(c, user)
                for c in t.get('conditions', [])
            )
            available.append({
                **t,
                'conditions_met': conditions_met,
            })
        return available

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None

    def _evaluate_condition(self, condition: dict, user) -> bool:
        """Evaluate a transition condition."""
        ctype = condition.get('type', '')

        if ctype == 'user_has_role':
            if not user:
                return False
            role = condition.get('role', '')
            return user.groups.filter(name=role).exists() or user.is_superuser

        if ctype == 'field_equals':
            obj = self.content_object
            field = condition.get('field', '')
            value = condition.get('value')
            return getattr(obj, field, None) == value

        if ctype == 'field_in':
            obj = self.content_object
            field = condition.get('field', '')
            values = condition.get('values', [])
            return getattr(obj, field, None) in values

        if ctype == 'is_authenticated':
            return user and user.is_authenticated

        if ctype == 'is_superuser':
            return user and user.is_superuser

        # Unknown condition type — pass by default
        logger.warning(f'Unknown condition type: {ctype}')
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
            logger.warning(f'Failed to fire action {action}: {e}')

    def __str__(self):
        return f'{self.workflow.name}: {self.current_state} (obj={self.object_id})'


class TransitionLog(models.Model):
    """Immutable log of every state transition."""
    instance = models.ForeignKey(
        WorkflowInstance,
        on_delete=models.CASCADE,
        related_name='transition_logs',
    )
    from_state = models.CharField(max_length=100)
    to_state = models.CharField(max_length=100)
    transitioned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    note = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.from_state} → {self.to_state} at {self.timestamp}'


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

    class OnFailure(models.TextChoices):
        FAIL = "fail"
        RETRY = "retry"
        SKIP = "skip"
        ESCALATE = "escalate"

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
    # Ordered list of Skill slugs injected into the agent at dispatch time.
    skill_refs = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of Skill slugs injected at dispatch.",
    )
    # For FAN_OUT pattern: max parallel tasks to spawn. Null means derive
    # the count dynamically from the prior stage's output list length.
    fan_out_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Max parallel tasks for fan_out stages. Null → dynamic from prior output.",
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
