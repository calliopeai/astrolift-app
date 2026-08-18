"""Per-assignment state for the grants a managed-service binding declares (#1367).

A binding used to report ready as soon as its resource was provisioned. On a
cloud where authorization is a *separate object* from the identity — Azure
writes one ``Microsoft.Authorization/roleAssignments`` per grant — that says
nothing about whether the workload can actually reach the data plane. The
assignment can still be propagating, or ARM can have rejected it, and the
operator sees green while the app 403s.

One row per ``(binding, environment, role definition, scope)``: the grant the
binding declared, and what became of it the last time the workload-identity
activity reconciled. ``required`` therefore means "an assignment this binding
declared and the grant contract resolved to a real role" — a binding whose
grants all classify as control-plane work (the twelve Azure drivers that hand
the pod a projected Secret instead) resolves to *no* assignment and so has no
rows, which reads as ready rather than pending forever.

Rows exist only for clouds that write discrete assignment objects. On AWS and
GCP the grants are the identity's own inline policy / role list, written by the
same call that creates the identity: there is nothing that can be half-applied,
so there is nothing to track.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class WorkloadIdentityGrant(BaseCoreModel):
    class State(models.TextChoices):
        PENDING = "pending"
        APPLIED = "applied"
        FAILED = "failed"

    UNAPPLIED_STATES = (State.PENDING, State.FAILED)

    managed_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        related_name="workload_identity_grants",
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="workload_identity_grants",
        on_delete=models.CASCADE,
        help_text=(
            "The environment whose workload identity carries the assignment. A "
            "project-owned service attached to several environments is granted "
            "once per identity, and each can be in a different state."
        ),
    )
    provider_plugin_slug = models.CharField(max_length=64)
    identity_role_name = models.CharField(
        max_length=128,
        help_text="Identity the assignment binds (the UAMI / IAM role name).",
    )
    role_definition_id = models.CharField(max_length=128)
    role_name = models.CharField(max_length=128, blank=True, default="")
    scope = models.TextField()
    assignment_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=(
            "Provider-side name of the assignment; deterministic, so an operator "
            "can find the exact object in the portal. Empty when the attempt "
            "never got far enough to derive one."
        ),
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    reason = models.TextField(
        blank=True,
        default="",
        help_text="Why the grant is not applied. Empty once it is.",
    )
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["managed_service", "app_environment", "role_definition_id", "scope"],
                condition=models.Q(deleted_at__isnull=True),
                name="wi_grant_unique_active_per_binding_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["app_environment", "state"], name="wi_grant_env_state_idx"),
        ]


def grant_state_for(rows) -> str:
    """Collapse a binding's grants into the one word a surface renders.

    ``failed`` outranks ``pending`` because a rejected grant needs an operator
    and a propagating one needs a minute. ``not_required`` is the answer for a
    binding that declared no assignable grant at all, and is deliberately not
    the same word as ``applied``: "this cloud authorizes it another way" and
    "the role assignment is in place" are different facts.
    """
    states = {row.state for row in rows}
    if not states:
        return "not_required"
    if WorkloadIdentityGrant.State.FAILED in states:
        return WorkloadIdentityGrant.State.FAILED.value
    if WorkloadIdentityGrant.State.PENDING in states:
        return WorkloadIdentityGrant.State.PENDING.value
    return WorkloadIdentityGrant.State.APPLIED.value
