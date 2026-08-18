"""Vendor-neutral SMTP relay for the ``email`` kind on GCP (#1453).

GCP ships no first-party transactional email service, so ``email`` had no
executable variant there and the matrix carried a ``planned`` placeholder
called ``gcp_thirdparty`` -- the only planned variant in the whole matrix that
named no vendor. Every other one names a product. Naming a vendor now would be
inventing product direction: SendGrid, Mailgun, Postmark and Resend are all
defensible and the platform has never chosen between them.

It does not have to choose. Every third-party transactional sender speaks
SMTP, so a vendor-neutral SMTP relay reaches all of them and commits the
platform to none. It is portable by construction and behaves identically on
every cloud, which is the same move that made ``cache`` portable through an
in-cluster Memcached rather than a fourth managed cache. It is also, very
likely, what the vendor-neutral ``gcp_thirdparty`` name was reaching for. And
it forecloses nothing: a first-party vendor driver, if one is ever wanted,
arrives as an additional variant beside this one rather than as a replacement.

Generic SMTP is already a first-class transport for the control plane's own
mail (``TransportKind.SMTP`` in ``astrolift_operations/email_infra.py``, beside
SES, SendGrid and Postmark). This is the same answer, for tenant workloads.

Why the GCP plugin owns this rather than ``k8s_native``
-------------------------------------------------------
An SMTP relay is not a Kubernetes object. Every ``k8s_native`` managed-service
driver in this tree runs something inside the cluster -- a Memcached ring, a
CNPG cluster, a RabbitMQ broker -- and this one runs nothing. It carries an
operator-configured relay endpoint and credential references into a workload's
environment; registering it as in-cluster would claim a component that does not
exist.

The runtime settles it too. astrolift-app#1484 records that managed-service
lookup is ``plugins.get(<the cluster's own plugin>, "managed:<kind>:<variant>")``
with no fallback, so no ``k8s_native`` variant is reachable from a GKE-hosted
app today. Closing an "email on GCP" gap with a driver a GKE cluster cannot
resolve would reproduce the exact defect #1484 exists to report: a portability
rule the guard tests confirm and the runtime does not honour.

Nothing here is GCP-specific, though, and the file says so on purpose. The
driver imports no cloud SDK and calls no Google API, so registering the same
class in the AWS, Azure or ``k8s_native`` plugins is a one-line plugin change
whenever an install wants a relay beside (or instead of) its first-party
sender. That is left as its own change rather than smuggled into a gap ticket.

Operator-supplied credentials, never provisioned ones
------------------------------------------------------
A relay cannot mint an account with a third party, so there is nothing to
provision: the operator supplies the endpoint and the credentials once,
install-wide, through the cluster's ``provider_config``. Per
``_sdk/binding_policy.py`` the credentials arrive as *references* into the
install secrets backend and are forwarded untouched, so the driver never holds
the password and cannot spill it into ``ManagedServiceBinding.env_value_ref``,
a plaintext column. Those keys are listed in
``binding_policy.PASS_THROUGH_REFERENCES`` for the same reason FSx Windows is:
a driver that never learns a value is structurally unable to inline it.

Because one relay serves every app on the install, an app author must not be
able to send as an arbitrary domain through it. A per-service ``from_address``
is accepted only inside the operator's ``allowed_sender_domains``, and the
check fails closed: a malformed address, or one outside the allowlist, refuses
the provision rather than silently falling back to the operator default.

Binding
-------
Emits the same portable envelope as ``aws/managed/email_ses`` and
``azure/managed/email_acs`` -- ``EMAIL_PROVIDER`` / ``EMAIL_API_KEY`` /
``EMAIL_FROM_ADDRESS`` / ``EMAIL_REGION`` -- so a manifest moves between the
three without the workload reading different variables, plus ``SMTP_*``
aliases for a client that dials the relay directly. ``EMAIL_API_KEY`` is the
SMTP password, which is the credential slot the two siblings put their API key
and connection string in.

Lifecycle
---------
There is no backing resource, so the lifecycle is deliberately thin, in the
shape ``aws/managed/model_endpoint_bedrock`` uses for on-demand model access:
``provision`` validates and returns ``ready=True`` with nothing to poll, and a
syntactically valid handle is always available as long as the operator's relay
config still validates. ``deprovision`` deletes nothing -- the relay, its
mailboxes and its credentials are the operator's, and Astrolift never owned
them -- so both deletion flags are honoured by saying exactly that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
    unsupported_update,
)

KIND = "email"
VARIANT = "smtp"

#: Submission (587, STARTTLS) and implicit TLS (465). Plaintext SMTP is not an
#: option: the binding carries a password, and every hosted relay that issues
#: one offers at least one of these two.
TLS_MODES: frozenset[str] = frozenset({"starttls", "implicit"})

_ADDRESS = re.compile(r"^[^\s@]+@([^\s@.]+(?:\.[^\s@.]+)+)$")


class SMTPRelayError(Exception):
    """Distinct from the generic plugin error so the control plane can tell a
    managed-service failure from an infra-driver failure. Every instance here
    is an operator-actionable configuration problem, never a transient one."""


@dataclass(frozen=True)
class SMTPRelayConfig:
    """The relay the operator points this install at.

    Install-wide rather than per-service on purpose: the endpoint and the
    credentials are the operator's relationship with their sender, and an app
    author must not be able to redirect mail or name arbitrary secret paths.
    """

    host: str = ""
    username_secret_ref: str = ""
    password_secret_ref: str = ""
    default_from_address: str = ""

    port: int = 587
    tls_mode: str = "starttls"

    allowed_sender_domains: tuple[str, ...] = ()
    """Domains a per-service ``from_address`` may use. Empty means the domain
    of ``default_from_address`` and nothing else, which is the safe reading of
    an operator who never thought about it."""

    region: str = ""
    """Operator's label for where the relay terminates, passed through
    verbatim. SMTP has no region; hosted senders do (``smtp.eu.mailgun.org``,
    ``email-smtp.us-east-1.amazonaws.com``), and ``EMAIL_REGION`` is part of
    the envelope both siblings emit, so the honest value is the operator's own
    rather than one this driver invents."""


class SMTPRelayEmailDriver(ManagedServiceDriver):
    def __init__(self, *, config: SMTPRelayConfig | None = None) -> None:
        # Deliberately no validation here. A misconfigured relay is an operator
        # problem that has to surface as a driver *result* the workflow can put
        # on the row, not as a crash while the plugin registry builds a driver.
        self._config = config or SMTPRelayConfig()

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="gcp",
        driver="email_smtp",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._assert_relay_configured()
            from_address = self._from_address(spec.config)
        except SMTPRelayError as exc:
            return ProvisionResult(ok=False, handle="", message=str(exc), errors=["invalid_smtp_relay_config"])
        return ProvisionResult(
            ok=True,
            handle=self._handle_for(spec),
            # Nothing was created, so there is no creating -> available
            # transition for the workflow to wait on.
            ready=True,
            message=(
                f"smtp relay {self._config.host}:{self._config.port} bound for {from_address}; "
                "no resource was created, the relay is operator-owned"
            ),
        )

    @driver_op(cloud="gcp", driver="email_smtp")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        # The relay itself is operator config, not service config, and the one
        # per-service field (from_address) is re-read from the row when the
        # binding is rendered. Rebinding is what applies it, and a reprovision
        # costs nothing here because nothing is destroyed.
        return unsupported_update(
            spec.handle,
            "an SMTP relay has no resource to modify: the endpoint and credentials are install-wide "
            "operator config, and the sender address is applied when the binding is re-rendered",
        )

    @driver_op(
        cloud="gcp",
        driver="email_smtp",
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
        # Both flags are answerable without branching: Astrolift provisioned
        # nothing, holds no message store, and has no guard of its own to
        # bypass. Saying that plainly beats a four-corner matrix over an empty
        # set, and it keeps teardown idempotent for the orphan scanners.
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"smtp relay binding released (delete_data={delete_data}, force_destroy={force_destroy}); "
                "the relay, its sending history and its credentials are operator-owned and were not touched"
            ),
        )

    @driver_op(cloud="gcp", driver="email_smtp")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._assert_handle(handle.handle)
            self._assert_relay_configured()
        except SMTPRelayError as exc:
            return ServiceStatus(handle=handle.handle, state="error", message=str(exc))
        # There is no remote object to be unavailable, and reachability is not
        # probed: a relay that rejects the credentials fails at send time, in
        # the workload, where the bounce is visible. Reporting "available" for
        # a valid handle over a configured relay is the whole claim.
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=(
                f"smtp relay {self._config.host}:{self._config.port} is configured; "
                "deliverability is not probed from the control plane"
            ),
        )

    @driver_op(cloud="gcp", driver="email_smtp")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        self._assert_handle(handle.handle)
        self._assert_relay_configured()
        from_address = self._from_address(config)
        cfg = self._config
        return Binding(
            env_vars={
                # Canonical contract envs (managed_service_kinds.py), same
                # four the SES and ACS drivers emit.
                "EMAIL_PROVIDER": ValueRef(literal=VARIANT),
                "EMAIL_API_KEY": ValueRef(secret_ref=cfg.password_secret_ref),
                "EMAIL_FROM_ADDRESS": ValueRef(literal=from_address),
                "EMAIL_REGION": ValueRef(literal=cfg.region),
                # SMTP-flavoured aliases, the same shape as the SES driver's
                # SES_* and the ACS driver's ACS_* set.
                "SMTP_HOST": ValueRef(literal=cfg.host),
                "SMTP_PORT": ValueRef(literal=str(cfg.port)),
                "SMTP_TLS_MODE": ValueRef(literal=cfg.tls_mode),
                "SMTP_USERNAME": ValueRef(secret_ref=cfg.username_secret_ref),
                "SMTP_PASSWORD": ValueRef(secret_ref=cfg.password_secret_ref),
            },
            # The control plane resolves these refs into the synthesized
            # Kubernetes binding Secret, and the relay authenticates with the
            # username and password rather than with a cloud identity, so
            # there is no cloud principal to grant anything to.
            iam_grants=[],
            notes=(
                "Vendor-neutral SMTP. EMAIL_API_KEY and SMTP_PASSWORD are the same operator-supplied "
                "secret reference; EMAIL_PROVIDER is 'smtp' so a workload that also runs on SES or ACS "
                "can branch on it. Deliverability, DKIM/SPF/DMARC and the sending reputation belong to "
                "the operator's relay, not to Astrolift."
            ),
        )

    @driver_op(cloud="gcp", driver="email_smtp")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError(
            "an SMTP relay binding holds no state to snapshot; the sending history lives with the relay operator",
        )

    @driver_op(cloud="gcp", driver="email_smtp")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError(
            "an SMTP relay binding holds no state to restore; re-provision against the configured relay",
        )

    # ---- read-only schemas --------------------------------------------

    @driver_op(cloud="gcp", driver="email_smtp", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": (
                        "Sender address for this service. Must sit inside the operator's "
                        "allowed sender domains; omitted means the operator default."
                    ),
                },
            },
        }

    @driver_op(cloud="gcp", driver="email_smtp", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EMAIL_PROVIDER": "Always 'smtp' for this driver",
                "EMAIL_API_KEY": "Secrets-backend ref to the SMTP password (alias of SMTP_PASSWORD)",
                "EMAIL_FROM_ADDRESS": "Sender address, operator default unless the service overrides it",
                "EMAIL_REGION": "Operator's label for where the relay terminates; empty when unset",
                "SMTP_HOST": "Relay hostname",
                "SMTP_PORT": "Relay port (587 submission, 465 implicit TLS)",
                "SMTP_TLS_MODE": "'starttls' or 'implicit'",
                "SMTP_USERNAME": "Secrets-backend ref to the SMTP username",
                "SMTP_PASSWORD": "Secrets-backend ref to the SMTP password",
            },
        )

    @driver_op(cloud="gcp", driver="email_smtp", heartbeat=False)
    def editable_fields(self) -> list[str]:
        # Nothing is applied by update(): see the comment there.
        return []

    # ---- internals ----------------------------------------------------

    def _assert_relay_configured(self) -> None:
        cfg = self._config
        missing = [
            name
            for name in ("host", "username_secret_ref", "password_secret_ref", "default_from_address")
            if not getattr(cfg, name)
        ]
        if missing:
            raise SMTPRelayError(
                f"smtp relay is not configured on this cluster: set {', '.join(missing)} in the provider config",
            )
        if cfg.tls_mode not in TLS_MODES:
            raise SMTPRelayError(
                f"smtp tls_mode must be one of {sorted(TLS_MODES)} (got {cfg.tls_mode!r}); "
                "plaintext SMTP would put the relay password on the wire",
            )
        if not 1 <= cfg.port <= 65535:
            raise SMTPRelayError(f"smtp port {cfg.port} is out of range")
        _domain_of(cfg.default_from_address, what="default_from_address")

    def _allowed_domains(self) -> frozenset[str]:
        explicit = {domain.strip().lower().lstrip("@") for domain in self._config.allowed_sender_domains}
        explicit.discard("")
        if explicit:
            return frozenset(explicit)
        return frozenset({_domain_of(self._config.default_from_address, what="default_from_address")})

    def _from_address(self, config: dict[str, Any] | None) -> str:
        requested = str((config or {}).get("from_address") or "").strip()
        if not requested:
            return self._config.default_from_address
        domain = _domain_of(requested, what="from_address")
        allowed = self._allowed_domains()
        if domain not in allowed:
            raise SMTPRelayError(
                f"from_address {requested!r} is outside the relay's allowed sender domains "
                f"({', '.join(sorted(allowed))}); one relay serves every app on this install",
            )
        return requested

    @staticmethod
    def _handle_for(spec: ProvisionSpec) -> str:
        parts = [
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "email",
        ]
        slug = "-".join(part for part in parts if part).lower()
        return f"{KIND}/{_safe(slug)[:64]}"

    @staticmethod
    def _assert_handle(handle: str) -> None:
        kind, _, name = handle.partition("/")
        if kind != KIND or not name:
            raise SMTPRelayError(f"handle {handle!r} must be '{KIND}/<name>'")


# ----- module-level helpers --------------------------------------------


def _domain_of(address: str, *, what: str) -> str:
    """The domain half, or a refusal. Never a best-effort split: the domain is
    what the allowlist is checked against, so a value this cannot parse must
    not reach the binding."""
    match = _ADDRESS.match(address.strip())
    if match is None:
        raise SMTPRelayError(f"{what} {address!r} is not a valid email address")
    return match.group(1).lower()


def _safe(value: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in value.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")
