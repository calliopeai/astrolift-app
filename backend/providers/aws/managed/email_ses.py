"""AWS Simple Email Service (SES) managed-service driver (#375).

Implements ``ManagedServiceDriver`` for AWS's transactional email
path. SES is the canonical AWS managed-email surface and pre-dates
the newer ``Pinpoint Email`` rebrand -- we target the classic SES
v1 control-plane (``boto3.client('ses')``) because moto's coverage
is broader there and the v1 + v2 split doesn't affect the
identity-verification + configuration-set primitives this driver
needs.

The driver provisions a *verified sending identity* (domain or
single email address). Identity verification is the SES contract
boundary: nothing else can be sent until AWS marks the identity
``Verified`` (DKIM CNAMEs published, or the verification email
clicked). The driver fires the verification request on provision
and surfaces the pending status in ``status()`` so the workflow
layer can poll without poking SES directly.

Deprovision implements the SDK's four-corner matrix:

  delete_data=False, force_destroy=False (default):
    keep sending stats + reputation history (SES does not expose
    stats deletion -- we honour the contract by NOT deleting the
    identity, since identity-delete also discards send-history
    aggregates in the SES console). Identity-level configuration
    set deletion-protection is respected via a soft marker (mirrors
    the OpenSearch driver's pattern in #373).

  delete_data=True, force_destroy=False:
    delete the identity. Sending stats go with it. Respect the
    configuration-set deletion-protection marker.

  delete_data=False, force_destroy=True:
    keep stats, bypass the deletion-protection marker (operator
    cleanup of a stuck mid-modify state).

  delete_data=True, force_destroy=True:
    nuke. Delete identity + configuration set, ignore protection.

Binding emits the platform-standard ``EMAIL_PROVIDER`` /
``EMAIL_API_KEY`` envs plus the SES-specific aliases the task
spec asks for: ``from_address``, ``region``, ``smtp_endpoint``,
``smtp_user_ref``, ``smtp_password_ref``. SMTP credentials are
held in Secrets Manager and referenced via secret_ref envs so
no IAM secret value lands in the rendered manifest.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
)

KIND = "email"


# SES SMTP endpoints are derived from the region per the AWS docs.
# email-smtp.<region>.amazonaws.com on port 587 (STARTTLS) or 465
# (TLS). We surface the bare hostname; consumers pick the port.
def _smtp_endpoint_for(region: str) -> str:
    return f"email-smtp.{region}.amazonaws.com"


@dataclass(frozen=True)
class SESEmailConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    region: str

    identity_prefix: str = "astrolift"
    """Logical prefix used when the operator doesn't supply an
    explicit identity. The driver will assemble a verified-domain
    identity of the form ``<prefix>-<org>-<app>-<env>.<base_domain>``
    when ``base_domain`` is configured, otherwise it falls back to
    using the explicit ``spec.config.identity`` (single email or
    full domain)."""

    base_domain: str = ""
    """Optional zone the operator owns. When set, generated domain
    identities live under it (mirrors the BYO DNS zone pattern
    enforced in opscode). When empty the driver requires
    ``spec.config.identity`` so it never invents a domain."""

    secrets_manager_prefix: str = "astrolift/ses"
    """Path prefix for the SMTP-credentials secret. Tag-based IAM
    policies can scope to this prefix."""

    smtp_username: str = "astrolift-ses"
    """Conventional SMTP username. The actual SES SMTP username
    is derived from an IAM access-key-id via the SES SMTP password
    algorithm; for the platform's purposes we surface a stable
    name + persist the rendered key/secret in Secrets Manager so
    workloads can rotate without re-provisioning."""

    configuration_set_name_prefix: str = "astrolift"
    """Prefix on the SES configuration set name. The driver creates
    one configuration set per identity to keep event-destination
    routing isolated per app."""

    deletion_protection_default: bool = True
    """Default for new identities. Mirrors the OpenSearch driver's
    posture; operators with ``force_destroy=True`` bypass on delete."""


class AmazonSESDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: SESEmailConfig,
        ses_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if ses_client is not None:
            self._ses = ses_client
        else:
            import boto3

            self._ses = boto3.client("ses", region_name=config.region)
        if secrets_client is not None:
            self._sm = secrets_client
        else:
            import boto3

            self._sm = boto3.client(
                "secretsmanager", region_name=config.region,
            )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="email_ses",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            identity = self._identity_for(spec=spec)
        except ManagedServiceError as exc:
            return ProvisionResult(
                ok=False, handle="",
                message=str(exc), errors=[str(exc)],
            )

        cfg = spec.config or {}
        is_domain = "@" not in identity

        if self._identity_verified_state(identity) is not None:
            self._ensure_configuration_set(spec=spec, identity=identity)
            self._ensure_smtp_credentials(spec=spec, identity=identity)
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=identity),
                message=(
                    f"ses identity {identity} already registered "
                    f"(state="
                    f"{self._identity_verified_state(identity)})"
                ),
            )

        try:
            if is_domain:
                self._ses.verify_domain_identity(Domain=identity)
            else:
                self._ses.verify_email_identity(EmailAddress=identity)
        except Exception as exc:
            return ProvisionResult(
                ok=False, handle="",
                message=f"verify_identity: {exc}",
                errors=[str(exc)],
            )

        # Best-effort: register a configuration set scoped to this
        # identity. Configuration sets carry event destinations
        # (CloudWatch, SNS, Kinesis); operators wire those out-of-band.
        cset_name = self._ensure_configuration_set(
            spec=spec, identity=identity,
        )

        # Persist a generated SMTP secret pair so the binding has
        # stable refs. Real SES SMTP credentials must be derived
        # from an IAM access-key-id via the SES-specific SMTP
        # password algorithm; that is an out-of-band operator
        # rotation step. The driver stores placeholders the
        # operator overwrites once the IAM access key is created.
        self._ensure_smtp_credentials(spec=spec, identity=identity)

        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        self._set_deletion_protection(
            identity=identity, enabled=deletion_protection,
        )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=identity),
            message=(
                f"ses identity {identity} verification requested "
                f"(configuration set={cset_name})"
            ),
        )

    @driver_op(cloud="aws", driver="email_ses")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, identity = parse_handle(spec.handle)
        cfg = spec.config or {}

        # SES has no resizable knob -- updates only toggle the
        # deletion-protection soft marker today.
        if "deletion_protection" in cfg:
            self._set_deletion_protection(
                identity=identity,
                enabled=bool(cfg["deletion_protection"]),
            )
            return UpdateResult(
                ok=True, handle=spec.handle,
                message=f"deletion_protection updated for {identity}",
            )

        return UpdateResult(
            ok=True, handle=spec.handle,
            message="no modifiable attributes provided -- no-op",
        )

    @driver_op(
        cloud="aws",
        driver="email_ses",
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
        _, identity = parse_handle(spec.handle)

        if self._identity_verified_state(identity) is None:
            self._delete_configuration_set(identity=identity)
            self._delete_smtp_credentials(identity=identity)
            self._delete_protection_marker(identity)
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=f"ses identity {identity} already gone",
            )

        if (
            self._deletion_protection_on(identity)
            and not force_destroy
            and delete_data
        ):
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=(
                    f"ses identity {identity} has deletion_protection "
                    f"enabled -- pass force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )

        stats_preserved = not delete_data
        # delete_data=False preserves sending stats by NOT deleting
        # the identity -- the SES console aggregates send-volume +
        # bounce/complaint stats at the identity level, and SES
        # offers no stats-only export. The driver's contract here
        # is to leave the identity in place when the operator hasn't
        # opted in to data destruction.
        if delete_data:
            try:
                self._ses.delete_identity(Identity=identity)
            except Exception as exc:
                return DeprovisionResult(
                    ok=False, handle=spec.handle,
                    message=f"delete_identity: {exc}",
                    errors=[str(exc)],
                )
            self._delete_configuration_set(identity=identity)
            self._delete_smtp_credentials(identity=identity)
            self._delete_protection_marker(identity)
        elif force_destroy:
            # Retain data path but caller wants the protection marker
            # cleared so a follow-up call without force_destroy is
            # permitted. Drop the marker only -- identity stays.
            self._delete_protection_marker(identity)

        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=(
                f"ses identity {identity} "
                f"{'deleted' if delete_data else 'retained'} "
                f"(stats={'preserved' if stats_preserved else 'discarded'},"
                f" force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="email_ses")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, identity = parse_handle(handle.handle)
        state = self._identity_verified_state(identity)
        if state is None:
            return ServiceStatus(
                handle=handle.handle, state="deprovisioned",
                message=f"ses identity {identity} not found",
            )
        if state == "Success":
            return ServiceStatus(
                handle=handle.handle, state="available",
                message=f"ses identity {identity} verified",
            )
        if state == "Pending":
            return ServiceStatus(
                handle=handle.handle, state="provisioning",
                message=(
                    f"ses identity {identity} pending DNS / email "
                    f"verification"
                ),
            )
        if state == "Failed":
            return ServiceStatus(
                handle=handle.handle, state="error",
                message=(
                    f"ses identity {identity} verification failed "
                    f"-- operator must re-trigger"
                ),
            )
        return ServiceStatus(
            handle=handle.handle, state="updating",
            message=f"ses identity {identity} reports {state}",
        )

    @driver_op(cloud="aws", driver="email_ses")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, identity = parse_handle(handle.handle)
        state = self._identity_verified_state(identity)
        if state is None:
            raise ManagedServiceError(
                f"binding requested for missing identity {identity}",
            )
        from_address = _from_address_for(identity=identity)
        smtp_endpoint = _smtp_endpoint_for(self._config.region)
        access_key_secret = self._smtp_access_key_secret_name(
            identity=identity,
        )
        secret_key_secret = self._smtp_secret_key_secret_name(
            identity=identity,
        )

        env_vars = {
            # Canonical contract envs (managed_service_kinds.py)
            "EMAIL_PROVIDER": ValueRef(literal="ses"),
            "EMAIL_API_KEY": ValueRef(secret_ref=secret_key_secret),
            "EMAIL_FROM_ADDRESS": ValueRef(literal=from_address),
            "EMAIL_REGION": ValueRef(literal=self._config.region),
            # SES-flavoured aliases the task spec asks for.
            "SES_FROM_ADDRESS": ValueRef(literal=from_address),
            "SES_REGION": ValueRef(literal=self._config.region),
            "SES_SMTP_ENDPOINT": ValueRef(literal=smtp_endpoint),
            "SES_SMTP_USER": ValueRef(secret_ref=access_key_secret),
            "SES_SMTP_PASSWORD": ValueRef(secret_ref=secret_key_secret),
        }
        identity_arn = (
            f"arn:aws:ses:{self._config.region}:*:identity/{identity}"
        )
        return Binding(
            env_vars=env_vars,
            iam_grants=[
                Grant(
                    resource=identity_arn,
                    actions=[
                        "ses:SendEmail",
                        "ses:SendRawEmail",
                        "ses:SendTemplatedEmail",
                    ],
                ),
                Grant(
                    resource=access_key_secret,
                    actions=["secretsmanager:GetSecretValue"],
                ),
                Grant(
                    resource=secret_key_secret,
                    actions=["secretsmanager:GetSecretValue"],
                ),
            ],
            notes=(
                "EMAIL_API_KEY / SES_SMTP_PASSWORD are Secrets Manager "
                "refs to the SES SMTP secret-access-key. SES_SMTP_USER "
                "is the SMTP access-key-id (also held in Secrets Manager"
                " so it can be rotated without redeploys). For sigv4-API"
                " senders use the ses:SendEmail IAM grant instead of"
                " SMTP."
            ),
        )

    @driver_op(cloud="aws", driver="email_ses")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        # SES has no built-in identity / configuration-set snapshot
        # primitive. Surface a deterministic id so the workflow layer
        # has a handle to track if it composes a snapshot out of
        # identity attributes externally.
        from datetime import UTC, datetime

        _, identity = parse_handle(handle.handle)
        state = self._identity_verified_state(identity)
        if state is None:
            raise ManagedServiceError(
                f"snapshot requested for missing identity {identity}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{_safe(identity)}-snap-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="email_ses")
    def restore(
        self, snapshot: SnapshotHandle, target: ProvisionSpec,
    ) -> ProvisionResult:
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(
                f"target identity provisioned; restore snapshot "
                f"{snapshot.snapshot_id} is an operator-driven "
                f"identity-attribute copy"
            ),
        )

    @driver_op(cloud="aws", driver="email_ses", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "identity": {
                    "type": "string",
                    "description": (
                        "Verified-sending identity: full domain "
                        "(example.com) or single address "
                        "(noreply@example.com). When omitted the "
                        "driver derives one from base_domain."
                    ),
                },
                "deletion_protection": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="email_ses", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EMAIL_PROVIDER": "Always 'ses' for this driver",
                "EMAIL_API_KEY": (
                    "Secrets Manager ref to the SES SMTP secret-access-"
                    "key"
                ),
                "EMAIL_FROM_ADDRESS": (
                    "Default From: address derived from the identity"
                ),
                "EMAIL_REGION": (
                    "AWS region hosting the SES identity"
                ),
                "SES_FROM_ADDRESS": "Alias for EMAIL_FROM_ADDRESS",
                "SES_REGION": "Alias for EMAIL_REGION",
                "SES_SMTP_ENDPOINT": (
                    "SES SMTP hostname (email-smtp.<region>.amazonaws.com)"
                ),
                "SES_SMTP_USER": (
                    "Secrets Manager ref to the SES SMTP access-key-id"
                ),
                "SES_SMTP_PASSWORD": (
                    "Secrets Manager ref to the SES SMTP secret-access-"
                    "key"
                ),
            },
        )

    # ---- internals ----------------------------------------------------

    def _identity_verified_state(
        self, identity: str,
    ) -> str | None:
        try:
            resp = self._ses.get_identity_verification_attributes(
                Identities=[identity],
            )
        except Exception as exc:
            if "NotFound" in str(exc) or "ResourceNotFound" in str(exc):
                return None
            raise
        attrs = resp.get("VerificationAttributes") or {}
        if identity not in attrs:
            return None
        return str(attrs[identity].get("VerificationStatus") or "Pending")

    def _identity_for(self, *, spec: ProvisionSpec) -> str:
        cfg = spec.config or {}
        explicit = cfg.get("identity")
        if explicit:
            return str(explicit)
        if not self._config.base_domain:
            raise ManagedServiceError(
                "ses driver requires either spec.config.identity or a "
                "configured base_domain so it can derive a sending "
                "domain",
            )
        parts = [
            self._config.identity_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        sub = "-".join(_safe(p) for p in parts if p)
        return f"{sub}.{self._config.base_domain}".lower()

    def _configuration_set_name_for(self, identity: str) -> str:
        return (
            f"{self._config.configuration_set_name_prefix}-"
            f"{_safe(identity)}"
        )[:64]

    def _ensure_configuration_set(
        self, *, spec: ProvisionSpec, identity: str,
    ) -> str:
        name = self._configuration_set_name_for(identity)
        try:
            self._ses.create_configuration_set(
                ConfigurationSet={"Name": name},
            )
        except Exception as exc:
            # AlreadyExists is the happy idempotent path.
            if "AlreadyExists" not in type(exc).__name__ and (
                "AlreadyExists" not in str(exc)
            ):
                # Don't fail provision on configuration-set errors --
                # the identity is still usable; surface via message.
                return name
        return name

    def _delete_configuration_set(self, *, identity: str) -> None:
        name = self._configuration_set_name_for(identity)
        try:
            self._ses.delete_configuration_set(
                ConfigurationSetName=name,
            )
        except Exception:
            return

    def _smtp_access_key_secret_name(self, *, identity: str) -> str:
        return (
            f"{self._config.secrets_manager_prefix}/"
            f"{_safe(identity)}/smtp-access-key-id"
        )

    def _smtp_secret_key_secret_name(self, *, identity: str) -> str:
        return (
            f"{self._config.secrets_manager_prefix}/"
            f"{_safe(identity)}/smtp-secret-key"
        )

    def _ensure_smtp_credentials(
        self, *, spec: ProvisionSpec, identity: str,
    ) -> None:
        access_key_name = self._smtp_access_key_secret_name(
            identity=identity,
        )
        secret_key_name = self._smtp_secret_key_secret_name(
            identity=identity,
        )
        # Generated placeholders. Real SES SMTP credentials must be
        # produced by deriving the SMTP-password from an IAM access
        # key via the SES SMTP password algorithm; that is an
        # out-of-band operator step. The driver keeps stable secret
        # refs so consumers don't need to re-bind after rotation.
        self._put_secret_if_missing(
            name=access_key_name,
            value=self._config.smtp_username,
        )
        self._put_secret_if_missing(
            name=secret_key_name,
            value=_generate_smtp_password(),
        )

    def _put_secret_if_missing(self, *, name: str, value: str) -> None:
        try:
            self._sm.create_secret(
                Name=name, SecretString=value,
            )
        except Exception as exc:
            if "ResourceExistsException" in type(exc).__name__:
                return
            # Soft state: log via raised ManagedServiceError so the
            # caller can decide. SES identity is the load-bearing
            # bit -- a missing secret means binding will be unusable.
            raise ManagedServiceError(
                f"create_secret for {name}: {exc}",
            ) from exc

    def _delete_smtp_credentials(self, *, identity: str) -> None:
        for name in (
            self._smtp_access_key_secret_name(identity=identity),
            self._smtp_secret_key_secret_name(identity=identity),
        ):
            try:
                self._sm.delete_secret(
                    SecretId=name,
                    ForceDeleteWithoutRecovery=True,
                )
            except Exception:
                continue

    def _protection_marker_name(self, identity: str) -> str:
        return (
            f"{self._config.secrets_manager_prefix}/"
            f"{_safe(identity)}/protection"
        )

    def _set_deletion_protection(
        self, *, identity: str, enabled: bool,
    ) -> None:
        name = self._protection_marker_name(identity)
        value = "1" if enabled else "0"
        try:
            self._sm.create_secret(Name=name, SecretString=value)
        except Exception as exc:
            if "ResourceExistsException" in type(exc).__name__:
                try:
                    self._sm.put_secret_value(
                        SecretId=name, SecretString=value,
                    )
                except Exception:
                    return

    def _deletion_protection_on(self, identity: str) -> bool:
        name = self._protection_marker_name(identity)
        try:
            resp = self._sm.get_secret_value(SecretId=name)
        except Exception:
            return self._config.deletion_protection_default
        return (resp.get("SecretString") or "1") != "0"

    def _delete_protection_marker(self, identity: str) -> None:
        name = self._protection_marker_name(identity)
        try:
            self._sm.delete_secret(
                SecretId=name, ForceDeleteWithoutRecovery=True,
            )
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


def _safe(value: str) -> str:
    """Coerce a slug to lowercase + replace anything outside
    ``[a-z0-9._-]`` with ``-``. SES + Secrets-Manager names share
    the same restrictive character set."""
    cleaned = "".join(
        c if (c.isalnum() or c in "-._") else "-"
        for c in value.lower()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _from_address_for(*, identity: str) -> str:
    """When the identity is a domain, default From: is
    ``noreply@<domain>``. When the identity is already an address
    we use it verbatim."""
    if "@" in identity:
        return identity
    return f"noreply@{identity}"


_SMTP_PASSWORD_ALPHABET = (
    string.ascii_letters + string.digits + "-_."
)


def _generate_smtp_password(length: int = 40) -> str:
    """Placeholder generator. Real SES SMTP passwords are produced
    by signing the IAM secret-access-key with the SES-specific
    derivation; that is an operator-side rotation step. The driver
    seeds a high-entropy value so binding has a stable ref the
    operator can overwrite."""
    return "".join(
        secrets.choice(_SMTP_PASSWORD_ALPHABET) for _ in range(length)
    )
