"""Curated allow-list of capability keys this install ships (#479).

The capability list is what the ``astroliftServerInfo`` handshake returns
to multi-install clients (mobile, CLI, SDK) so they can decide which UI
to render without pinging every feature endpoint and reacting to schema
errors.

Why an allow-list and not a derived set:

* Feature flags toggle runtime behaviour; capabilities advertise *what a
  client may attempt against this install*. A flag can flip off without
  the capability disappearing (e.g. a temporary maintenance hold).
* The set has to be stable across releases — clients pin to capability
  names, not to internal module paths.
* Capabilities are intentionally PUBLIC. They're served to unauthenticated
  callers. Nothing here may leak operator config.

Convention:

* snake_case
* grouped by surface with a dot separator — ``audit.export``,
  ``console.exec``, ``approvals.bulk``
* names live forever once shipped; removing a key is a breaking change
  for any client that gated UI on it
"""

from __future__ import annotations

from typing import Final

# Surface-grouped capability keys.
#
# Add a new entry here when the install ships a new operator-visible
# surface that clients should be able to detect. Removing an entry is
# a breaking change — bump the apiVersion fingerprint and announce.
SHIPPED_CAPABILITIES: Final[tuple[str, ...]] = (
    "models.hugging_face_catalogue",
    "models.deployment_observations",
    "models.cluster_density",
    "models.shared_prompt_relay",
    # Audit log surfaces (#293, #310, #383, etc.).
    "audit.read",
    "audit.export",
    "audit.payload_diff",
    "audit.retention_policy",
    # Approval flows for deploys + bulk operator actions.
    "approvals.bulk",
    "approvals.magic_link",
    "approvals.self_approve_toggle",
    # Console / cluster exec surfaces.
    "console.exec",
    "console.log_tail",
    "observability.exact_environment_logs",
    "observability.scoped_trace_envelopes",
    # Secrets handling surfaces.
    "secrets.reveal",
    "secrets.rotate",
    # Cluster lifecycle.
    "clusters.adopt",
    "clusters.teardown",
    "clusters.capabilities_probe",
    "clusters.reviewed_agent_install",
    "clusters.reviewed_log_collector_install",
    "apps.dependency_context",
    "providers.reference_read",
    "workloads.environment_targets",
    "previews.exact_identity",
    "previews.reviewed_routes",
    "previews.reviewed_live_logs",
    "previews.reviewed_log_exports",
    "previews.explicit_runtime_cost",
    # Deploy pipeline (start/promote/rollback/teardown).
    "deploys.start",
    "deploys.promote",
    "deploys.rollback",
    "deploys.teardown",
    # Forms (#453).
    "forms.builder",
    "forms.public_submit",
    # Workflow runtime surfaces.
    "workflows.temporal",
    "workflows.cancel",
    "workflows.terminate",
    "workflows.signal",
    "workflows.reviewed_definition_starts",
    "workflows.definition_input_contracts",
    "workflows.definition_start_recovery",
    "workflows.bounded_review_loops",
    "workflows.serial_collections",
    "pipelines.reviewed_starts",
    "pipelines.versioned_start_requests",
    "pipelines.start_request_recovery",
    "pipelines.exact_execution_cancellation",
    "pipelines.bounded_run_details",
    # SCM integration surfaces.
    "scm.github_app",
    "scm.webhook_secret_rotation",
    # Identity / auth surfaces clients probe pre-login.
    "identity.session_cookie",
    "identity.invitation_accept",
    "identity.organization_switch",
    # Webhook subscription surfaces.
    "webhooks.subscribe",
    "webhooks.secret_rotation",
    "agents.completion_callbacks",
    "agents.completion_callback_redelivery",
    # Notifications.
    "notifications.in_app",
    "notifications.email",
    # File uploads.
    "uploads.presigned",
    "models.admin_hosting",
    "models.huggingface_connections",
    "models.local_artifacts",
    # Federation between Astrolift installs (#411 family).
    "federation.peer_register",
    "federation.jwks_serve",
    # Server-info handshake itself — clients can detect old installs
    # that don't expose the handshake by the absence of this key from
    # an introspection-derived list, or its presence here on supported
    # installs.
    "server_info.handshake",
)


def is_known_capability(key: str) -> bool:
    """True if ``key`` is in the shipped allow-list."""
    return key in SHIPPED_CAPABILITIES


def shipped_capabilities() -> list[str]:
    """Return a fresh list of every shipped capability key, sorted for
    stable client-side diffing."""
    return sorted(SHIPPED_CAPABILITIES)


__all__ = [
    "SHIPPED_CAPABILITIES",
    "is_known_capability",
    "shipped_capabilities",
]
