"""GCP transactional-email managed-service stub driver (#375).

GCP ships no first-party transactional email service. The Google
Workspace ``Gmail API`` is a tenant-bound mailbox surface, not a
transactional-email sender; ``Cloud Pub/Sub`` and ``Cloud Tasks``
are not email transports; and the historical ``App Engine Mail
API`` was deprecated in 2021. The Google-recommended path is to
provision a third-party transactional sender -- SendGrid,
Mailgun, Postmark, or Resend -- and let workloads talk to its
API directly.

This stub keeps the (kind, variant) catalog complete so the
platform's matrix validator (#26) recognises GCP as covering the
``email`` kind. Every lifecycle entry raises ``NotImplementedError``
with a clear operator message pointing at the third-party path.
When the matrix learns to model "external provider with API key"
this file can flip to a real binding-only driver behind the same
class name; no plugin-registration change required at that point.

Mirrors the pattern shipped in ``gcp/managed/search_elastic_cloud``
(#373) on purpose -- the GCP stub for first-party-missing kinds is
a deliberate convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
)

KIND = "email"


_UNSUPPORTED_MSG = (
    "GCP doesn't ship a first-party transactional email service -- "
    "provision SendGrid / Mailgun / Postmark / Resend via the operator"
    " portal and wire its API key into the EMAIL_API_KEY binding"
    " env var"
)


@dataclass(frozen=True)
class GCPEmailStubConfig:
    """No fields today. Kept as a dataclass so a future real driver
    (e.g. a binding-only adapter for a third-party API key held in
    Secret Manager) can grow config without breaking the plugin
    registration."""

    project_id: str = ""


class GCPEmailStubDriver:
    """Stub driver for the GCP ``email`` slot.

    Implements the ``ManagedServiceDriver`` shape but raises
    ``NotImplementedError`` on lifecycle calls. Read-only schemas
    are populated so docs + binding contracts stay accurate."""

    def __init__(
        self,
        *,
        config: GCPEmailStubConfig | None = None,
    ) -> None:
        self._config = config or GCPEmailStubConfig()

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="gcp",
        driver="email_thirdparty_stub",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="email_thirdparty_stub")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(
        cloud="gcp",
        driver="email_thirdparty_stub",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="email_thirdparty_stub")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="email_thirdparty_stub")
    def binding(self, handle: ServiceHandle) -> Binding:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="email_thirdparty_stub")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="email_thirdparty_stub")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    # ---- read-only schemas (kept accurate so docs render) -------------

    @driver_op(cloud="gcp", driver="email_thirdparty_stub", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "description": (
                "Stub: GCP email variant is not yet implemented. "
                "Provisioning will raise NotImplementedError. "
                "Use SendGrid / Mailgun / Postmark / Resend."
            ),
            "properties": {},
        }

    @driver_op(cloud="gcp", driver="email_thirdparty_stub", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EMAIL_PROVIDER": (
                    "Third-party provider name (sendgrid / mailgun / "
                    "postmark / resend) -- not yet implemented"
                ),
                "EMAIL_API_KEY": (
                    "Secret Manager ref to the provider API key "
                    "(not yet implemented)"
                ),
                "EMAIL_FROM_ADDRESS": (
                    "Default From: address (not yet implemented)"
                ),
                "EMAIL_REGION": (
                    "Provider region (not yet implemented)"
                ),
            },
        )
