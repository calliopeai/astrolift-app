"""AgentBox — one live instance of a ``run_mode == persistent`` agent (#128).

An AgentTask is a batch run: it is dispatched, it does a thing, it ends. A box
is the opposite shape. Nothing runs it; it runs so that a human or a relay can
attach to it, and it ends when it stops being used.

The row is deliberately thin. Everything that describes *what* the box runs
(image, secret packet, runtime) already lives on ``AgentEnvironmentSpec``, and
everything about *how* an agent runs lives on ``Workload``'s run spec. This
model carries only what an instance needs: who owns it, whether it is up, where
its pod is, and when it should be reaped.

``owner`` is what makes the IDE's one-button gesture idempotent. The button
sends "ensure a box for me on this agent", and the ensure resolver looks up
``(organization, owner, environment_spec)`` — so a second click attaches to the
box already running rather than starting a rival one on another node.
"""

from __future__ import annotations

from _sdk.agent_session import DEFAULT_IDLE_TIMEOUT_SECONDS
from django.conf import settings
from django.db import models

from core.models.base import NamedBaseCoreModel


class AgentBox(NamedBaseCoreModel):
    class Status(models.TextChoices):
        # The row exists, nothing has been applied to a cluster yet.
        PENDING = "pending"
        # Manifests applied; the pod has not reported Running.
        PROVISIONING = "provisioning"
        # Attachable.
        RUNNING = "running"
        # An operator destroyed it (``destroyAgentBox``).
        STOPPED = "stopped"
        # The pod's keep-alive loop ended the session — the idle timeout
        # fired, or whoever was attached exited the shell inside tmux.
        # Distinct from STOPPED so an operator can tell "I killed it" from
        # "it reaped itself", which is the whole point of the timeout.
        EXPIRED = "expired"
        # The pod failed, or the spawn never got off the ground.
        FAILED = "failed"

    #: Statuses in which the box may still be holding a node. The reaper
    #: sweeps exactly these; everything else has already settled.
    LIVE_STATUSES: frozenset[str] = frozenset(
        {
            Status.PENDING,
            Status.PROVISIONING,
            Status.RUNNING,
        }
    )

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="agent_boxes",
        on_delete=models.CASCADE,
    )
    # The scope a box runs in (#1866), taken from what launched it, as an
    # agent task's is: a recorded project, else a recorded team, else the
    # agent's app through ``agent_definition``, else the org. A box ensured
    # from an agent records nothing here and belongs to the agent's app; one
    # ensured from a spec alone records the spec's owner. An org-level box is
    # reached by org-level grants only, so losing an owner narrows access
    # rather than widening it.
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="agent_boxes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="agent_boxes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Nullable so a box outlives the user record it was ensured for; a
    # box with no owner is still an org resource and still reapable.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="agent_boxes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # The persistent-mode Workload this box was ensured from, when one was
    # named. Optional: a box can be ensured from an environment spec alone,
    # which is the path the IDE button takes (``astro box ensure --agent
    # claude`` knows a runtime, not a registered workload).
    agent_definition = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="agent_boxes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Where the image and the secret packet come from. SET_NULL so deleting
    # a spec leaves a running box addressable long enough to be torn down.
    environment_spec = models.ForeignKey(
        "astrolift_agents.AgentEnvironmentSpec",
        related_name="agent_boxes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    # Seconds of no attached client AND no pane output before the in-pod
    # keep-alive loop kills the session and the pod exits. ``0`` is the
    # explicit "never reap" sentinel from ``_sdk.agent_session.NEVER``.
    idle_timeout_seconds = models.PositiveIntegerField(default=DEFAULT_IDLE_TIMEOUT_SECONDS)
    # Frozen at spawn so the box stays self-describing after the spec that
    # produced it is edited or deleted.
    image = models.CharField(max_length=512, blank=True, default="")
    # The k8s Job name and the namespace it landed in, frozen at spawn. The
    # namespace is stored rather than recomputed for the same reason
    # AgentTask stores it (#891): a recomputed guess can miss the pod.
    external_id = models.CharField(max_length=255, blank=True, default="")
    namespace = models.CharField(max_length=255, blank=True, default="")
    # Observed, not frozen: the reaper stamps it when it sees the Job's pod
    # running and clears it on restart (#129). A fast path for clients, never
    # the source of truth — it is blank until the first sweep after the pod
    # comes up, so anything that needs a pod now still resolves one.
    pod_name = models.CharField(max_length=255, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    # Last time someone opened a session inside this box through the exec
    # relay (#129), which is the closest the control plane gets to "when did
    # someone last ask for a way in".
    # Advisory only: real idleness is measured inside the pod from tmux pane
    # activity, because a detached agent that is working is not idle and the
    # control plane cannot see that from out here.
    last_attached_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    # The Zentinelle agent whose key a model-gateway box holds (#1851), and
    # when that key expires unless the reaper renews it first.
    model_gateway_agent_id = models.CharField(max_length=100, blank=True, default="")
    model_gateway_expires_at = models.DateTimeField(null=True, blank=True)
    # When Zentinelle stops honouring renewals of that key: its lifetime cap.
    model_gateway_lifetime_ends_at = models.DateTimeField(null=True, blank=True)
    # The connection whose install minted the key, as on AgentTask.
    model_gateway_connection = models.ForeignKey(
        "astrolift_operations.ZentinelleConnection",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="agent_box_slug_unique_active_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="agent_box_org_status_idx"),
        ]

    def __str__(self) -> str:
        return f"AgentBox {self.slug} ({self.status})"

    @property
    def is_live(self) -> bool:
        return self.status in self.LIVE_STATUSES
