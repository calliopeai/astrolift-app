"""AgentConversationTransfer: a managed SDK conversation moving into Astrolift (#1971).

The IDE moves a Calliope AI managed conversation to an Astrolift session with
paused admission, keyed by the IDE's ``transferId``:

    preparing          waiting for the envelope upload, and again once a
                       paused task is installing it (Git state, progress,
                       undo history);
    validating         a worker is checking the uploaded envelope before it
                       admits the paused task;
    destination_ready  the runner reported the exact install; the IDE may
                       stop its source and commit;
    committed          the session starts from the installed state, once;
    aborted            the arrival was retired and its stored envelope and
                       paused task removed.

The full envelope (record, snapshot and its transport) lives in the blob
store under ``envelope_key``; this row carries the acknowledged record, the
digests that bind every later call to that exact envelope, and the runner's
install progress and receipt.

``admission_deadline`` bounds the whole paused admission: a transfer still
preparing, validating or destination_ready past it is retired by the expiry
sweep, and the paused task's own timeout only starts at commit.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AgentConversationTransfer(BaseCoreModel):
    class State(models.TextChoices):
        PREPARING = "preparing"
        VALIDATING = "validating"
        DESTINATION_READY = "destination_ready"
        COMMITTED = "committed"
        ABORTED = "aborted"

    # Not yet committed or aborted: still holding a stored envelope and,
    # once admitted, a paused task.
    OPEN_STATES = frozenset({State.PREPARING, State.VALIDATING, State.DESTINATION_READY})

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="agent_conversation_transfers",
        on_delete=models.CASCADE,
    )
    agent_definition = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="conversation_transfers",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    environment_spec = models.ForeignKey(
        "astrolift_agents.AgentEnvironmentSpec",
        related_name="conversation_transfers",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # The paused session hosting the arrival. Set in the same transaction
    # that creates the task, so the spawner always finds it.
    agent_task = models.OneToOneField(
        "astrolift_agents.AgentTask",
        related_name="conversation_transfer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    transfer_id = models.CharField(max_length=256)
    source_task_id = models.CharField(max_length=256)
    state = models.CharField(max_length=32, choices=State.choices, default=State.PREPARING)
    # The IDE's TaskTransferRecord as this receiver last acknowledged it.
    record = models.JSONField(default=dict)
    snapshot_sha256 = models.CharField(max_length=64, blank=True, default="")
    envelope_key = models.CharField(max_length=512, blank=True, default="")
    envelope_sha256 = models.CharField(max_length=64, blank=True, default="")
    envelope_bytes = models.PositiveBigIntegerField(default=0)
    # Digests the runner's install receipt must repeat.
    expected_receipt = models.JSONField(default=dict, blank=True)
    receipt = models.JSONField(default=dict, blank=True)
    progress = models.JSONField(default=list, blank=True)
    # When the paused admission must have committed by; set at creation.
    admission_deadline = models.DateTimeField(db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    aborted_at = models.DateTimeField(null=True, blank=True)
    abort_reason = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            # Kept after soft deletion: a transferId names one arrival forever.
            models.UniqueConstraint(
                fields=["organization", "transfer_id"],
                name="agent_conv_transfer_org_id_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"AgentConversationTransfer {self.transfer_id} ({self.state})"
