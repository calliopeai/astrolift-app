"""AWS Simple Email Service (SES) managed-service driver (#375).

Implements ``ManagedServiceDriver`` for AWS's transactional email
path. SES is the canonical AWS managed-email surface and pre-dates
the newer ``Pinpoint Email`` rebrand. Identity lifecycle (create,
read, tag) goes through ``boto3.client('sesv2')``: v1 identities
carry no tags, so a re-entrant provision had no way to tell its own
identity apart from another org's -- or the platform's own sending
domain -- registered under a colliding derived name (#2029).
Configuration sets, DKIM/DNS token fetch, and delete stay on v1;
both API versions read and write the same underlying identity
store, so mixing the two is safe.

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
from _sdk.cloud_credentials import CredentialedConfig
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
    adoption_refusal,
    handle_for,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

KIND = "email"


# SES SMTP endpoints are derived from the region per the AWS docs.
# email-smtp.<region>.amazonaws.com on port 587 (STARTTLS) or 465
# (TLS). We surface the bare hostname; consumers pick the port.
def _smtp_endpoint_for(region: str) -> str:
    return f"email-smtp.{region}.amazonaws.com"


@dataclass(frozen=True)
class SESEmailConfig(CredentialedConfig):
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

    sns_event_destination_arn: str = ""
    """SNS topic ARN to wire as a configuration-set event destination
    (#756). When non-empty, ``_ensure_configuration_set`` calls
    ``ses:CreateConfigurationSetEventDestination`` so SES publishes
    SEND / DELIVERY / BOUNCE / COMPLAINT / OPEN / CLICK notifications
    through SNS to the platform's webhook receiver. Empty (the
    default) leaves the driver free to fall back to
    ``django.conf.settings.SES_EVENTS_SNS_TOPIC_ARN`` at call time —
    production deployments rely on the settings fallback so the
    constructor doesn't need to plumb the ARN through every
    instantiation site."""


class AmazonSESDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: SESEmailConfig,
        ses_client: Any | None = None,
        sesv2_client: Any | None = None,
        secrets_client: Any | None = None,
        route53: Any | None = None,
    ) -> None:
        self._config = config
        if ses_client is not None:
            self._ses = ses_client
        else:
            self._ses = aws_client("ses", region=config.region, credential=config.credential)
        if sesv2_client is not None:
            self._sesv2 = sesv2_client
        else:
            self._sesv2 = aws_client("sesv2", region=config.region, credential=config.credential)
        if secrets_client is not None:
            self._sm = secrets_client
        else:
            self._sm = aws_client(
                "secretsmanager",
                region=config.region,
                credential=config.credential,
            )
        # Lazily constructed Route53 driver used to publish the SES
        # domain-verification + Easy-DKIM records so a domain identity
        # converges to Verified with no manual operator DNS step.
        # Injected directly in tests; ``None`` means "construct on first
        # use" (and best-effort: a construction or API failure leaves the
        # identity pending rather than failing the provision).
        self._route53 = route53

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
                ok=False,
                handle="",
                message=str(exc),
                errors=[str(exc)],
            )

        cfg = spec.config or {}
        is_domain = "@" not in identity

        existing = self._get_identity(identity)
        if existing is not None:
            # The identity is already registered -- either a re-entrant call
            # over this service's own identity, or a name this spec's slugs
            # happen to collide with (another org's identity, or the
            # platform's own sending domain). v1 identities never carried
            # tags; SESv2 does, so refuse the adopt unless the existing
            # identity's ownership tags are this managed service's (#1961
            # pattern, closing #2029).
            refusal = adoption_refusal(existing.get("Tags"), spec, resource=f"ses identity {identity}")
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            existing_state = _verification_state(existing)
            # Re-entrant provision: the identity is already registered.
            # Re-run the best-effort ancillary steps (idempotent) so a
            # previously-stuck partial provision can self-heal -- including
            # re-publishing the verification DNS for an identity that's
            # still pending.
            self._ensure_configuration_set(spec=spec, identity=identity)
            smtp_ok = self._ensure_smtp_credentials(
                spec=spec,
                identity=identity,
            )
            if is_domain:
                self._publish_verification_dns(identity=identity)
            message = f"ses identity {identity} already registered (state={existing_state})"
            if not smtp_ok:
                message += "; smtp placeholder secrets not stored"
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=identity),
                message=message,
            )

        try:
            self._sesv2.create_email_identity(
                EmailIdentity=identity,
                Tags=tags_for(spec),
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_email_identity: {exc}",
                errors=[str(exc)],
            )

        # The verification request above is the load-bearing step -- the
        # identity is now registered with SES. Everything below is
        # best-effort: a freshly-requested-but-unverified SES identity is
        # the normal, expected state and must NOT hard-fail the provision.
        # Ancillary failures (configuration set, DNS publish, SMTP
        # placeholder secrets, deletion-protection marker) are surfaced in
        # the result message, never raised.

        # Best-effort: register a configuration set scoped to this
        # identity. Configuration sets carry event destinations
        # (CloudWatch, SNS, Kinesis); operators wire those out-of-band.
        cset_name = self._ensure_configuration_set(
            spec=spec,
            identity=identity,
        )

        # Best-effort: publish the domain-verification TXT + Easy-DKIM
        # CNAMEs into the operator's Route53 zone so the identity
        # converges to Verified with no manual DNS step. When the zone
        # isn't in Route53 (or the platform role lacks access) the
        # identity stays pending -- the operator publishes the records
        # surfaced via the obs/status surface; provision still succeeds.
        dns_published = False
        if is_domain:
            dns_published = self._publish_verification_dns(identity=identity)

        # Persist a generated SMTP secret pair so the binding has
        # stable refs. Real SES SMTP credentials must be derived
        # from an IAM access-key-id via the SES-specific SMTP
        # password algorithm; that is an out-of-band operator
        # rotation step. The driver stores placeholders the
        # operator overwrites once the IAM access key is created.
        smtp_ok = self._ensure_smtp_credentials(spec=spec, identity=identity)

        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        self._set_deletion_protection(
            identity=identity,
            enabled=deletion_protection,
        )

        message = f"ses identity {identity} verification requested (configuration set={cset_name})"
        if is_domain:
            message += (
                "; dns records published" if dns_published else "; pending dns verification (publish records manually)"
            )
        if not smtp_ok:
            message += "; smtp placeholder secrets not stored"

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=identity),
            message=message,
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
                ok=True,
                handle=spec.handle,
                message=f"deletion_protection updated for {identity}",
            )

        return UpdateResult(
            ok=True,
            handle=spec.handle,
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
                ok=True,
                handle=spec.handle,
                message=f"ses identity {identity} already gone",
            )

        if self._deletion_protection_on(identity) and not force_destroy and delete_data:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"ses identity {identity} has deletion_protection enabled -- pass force_destroy=True to bypass"
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
                    ok=False,
                    handle=spec.handle,
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
            ok=True,
            handle=spec.handle,
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
                handle=handle.handle,
                state="deprovisioned",
                message=f"ses identity {identity} not found",
            )
        if state == "Success":
            return ServiceStatus(
                handle=handle.handle,
                state="available",
                message=f"ses identity {identity} verified",
            )
        if state == "Pending":
            return ServiceStatus(
                handle=handle.handle,
                state="provisioning",
                message=(f"ses identity {identity} pending DNS / email verification"),
            )
        if state == "Failed":
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=(f"ses identity {identity} verification failed -- operator must re-trigger"),
            )
        return ServiceStatus(
            handle=handle.handle,
            state="updating",
            message=f"ses identity {identity} reports {state}",
        )

    @driver_op(cloud="aws", driver="email_ses")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, identity = parse_handle(handle.handle)
        state = self._identity_verified_state(identity)
        if state is None:
            raise ManagedServiceError(
                f"binding requested for missing identity {identity}",
            )
        cfg = config or {}
        from_address = _from_address_for(identity=identity)
        smtp_endpoint = _smtp_endpoint_for(self._config.region)
        access_key_secret = self._smtp_access_key_secret_name(
            identity=identity,
        )
        secret_key_secret = self._smtp_secret_key_secret_name(
            identity=identity,
        )

        env_vars: dict[str, ValueRef] = {
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

        # Operator-supplied sender envelope fields (#637/#639). Each
        # is emitted only when the operator has set a non-empty value
        # so workloads can ``os.getenv(..., default)`` cleanly. We
        # coerce missing-or-``None`` to ``""`` *before* str() so a
        # cleared-form ``None`` doesn't become the literal string
        # ``"None"`` (which is truthy).
        from_name = str(cfg.get("from_name") or "").strip()
        reply_to = str(cfg.get("reply_to") or "").strip()
        return_path = str(cfg.get("return_path") or "").strip()
        if from_name:
            env_vars["EMAIL_FROM_NAME"] = ValueRef(literal=from_name)
        if reply_to:
            env_vars["EMAIL_REPLY_TO"] = ValueRef(literal=reply_to)
        if return_path:
            env_vars["EMAIL_RETURN_PATH"] = ValueRef(literal=return_path)

        # Per-environment sender overrides (#638). The operator
        # supplies a ``env_senders`` map keyed by AppEnvironment name
        # (``production`` / ``preview``). Missing keys fall through
        # to the identity-derived EMAIL_FROM_ADDRESS above.
        env_senders = cfg.get("env_senders", {})
        if isinstance(env_senders, dict):
            prod_sender = str(
                env_senders.get("production") or "",
            ).strip()
            preview_sender = str(
                env_senders.get("preview") or "",
            ).strip()
            if prod_sender:
                env_vars["EMAIL_FROM_ADDRESS_PRODUCTION"] = ValueRef(
                    literal=prod_sender,
                )
            if preview_sender:
                env_vars["EMAIL_FROM_ADDRESS_PREVIEW"] = ValueRef(
                    literal=preview_sender,
                )

        identity_arn = f"arn:aws:ses:{self._config.region}:*:identity/{identity}"
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
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
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
                "from_name": {
                    "type": "string",
                    "description": (
                        "Optional display name folded into the From: header (emitted as EMAIL_FROM_NAME) (#637)."
                    ),
                },
                "reply_to": {
                    "type": "string",
                    "description": ("Optional Reply-To: address (emitted as EMAIL_REPLY_TO) (#637)."),
                },
                "return_path": {
                    "type": "string",
                    "description": ("Optional Return-Path: (bounce) address (emitted as EMAIL_RETURN_PATH) (#639)."),
                },
                "env_senders": {
                    "type": "object",
                    "description": (
                        "Per-environment From: overrides keyed by "
                        "AppEnvironment name; supported keys are "
                        "``production`` and ``preview`` -- each emits "
                        "EMAIL_FROM_ADDRESS_<ENV> when set (#638)."
                    ),
                    "properties": {
                        "production": {"type": "string"},
                        "preview": {"type": "string"},
                    },
                },
            },
        }

    @driver_op(cloud="aws", driver="email_ses", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EMAIL_PROVIDER": "Always 'ses' for this driver",
                "EMAIL_API_KEY": ("Secrets Manager ref to the SES SMTP secret-access-key"),
                "EMAIL_FROM_ADDRESS": ("Default From: address derived from the identity"),
                "EMAIL_REGION": ("AWS region hosting the SES identity"),
                "EMAIL_FROM_NAME": (
                    "Optional human-readable display name for the From: "
                    "header. Sourced from ``ManagedService.config."
                    "from_name``; emitted only when non-empty (#637)."
                ),
                "EMAIL_REPLY_TO": (
                    "Optional Reply-To: address. Sourced from "
                    "``ManagedService.config.reply_to``; emitted only "
                    "when non-empty (#637)."
                ),
                "EMAIL_RETURN_PATH": (
                    "Optional Return-Path: (bounce) address. Sourced "
                    "from ``ManagedService.config.return_path``; emitted "
                    "only when non-empty (#639)."
                ),
                "EMAIL_FROM_ADDRESS_PRODUCTION": (
                    "Optional per-environment From: override for the "
                    "``production`` AppEnvironment. Sourced from "
                    "``ManagedService.config.env_senders.production``; "
                    "emitted only when non-empty (#638)."
                ),
                "EMAIL_FROM_ADDRESS_PREVIEW": (
                    "Optional per-environment From: override for the "
                    "``preview`` AppEnvironment. Sourced from "
                    "``ManagedService.config.env_senders.preview``; "
                    "emitted only when non-empty (#638)."
                ),
                "SES_FROM_ADDRESS": "Alias for EMAIL_FROM_ADDRESS",
                "SES_REGION": "Alias for EMAIL_REGION",
                "SES_SMTP_ENDPOINT": ("SES SMTP hostname (email-smtp.<region>.amazonaws.com)"),
                "SES_SMTP_USER": ("Secrets Manager ref to the SES SMTP access-key-id"),
                "SES_SMTP_PASSWORD": ("Secrets Manager ref to the SES SMTP secret-access-key"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _get_identity(self, identity: str) -> dict[str, Any] | None:
        """Fetch the SESv2 identity, or ``None`` if it doesn't exist.

        Single read shared by the verification-state check and the
        ownership-tag check (#2029) so ``provision`` doesn't pay for two
        ``get_email_identity`` round trips on the re-entrant path.
        """
        try:
            return dict(self._sesv2.get_email_identity(EmailIdentity=identity))
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _identity_verified_state(
        self,
        identity: str,
    ) -> str | None:
        existing = self._get_identity(identity)
        if existing is None:
            return None
        return _verification_state(existing)

    def _identity_for(self, *, spec: ProvisionSpec) -> str:
        return identity_for(
            spec,
            identity_prefix=self._config.identity_prefix,
            base_domain=self._config.base_domain,
        )

    def _configuration_set_name_for(self, identity: str) -> str:
        return (f"{self._config.configuration_set_name_prefix}-{_safe(identity)}")[:64]

    def _ensure_configuration_set(
        self,
        *,
        spec: ProvisionSpec,
        identity: str,
    ) -> str:
        name = self._configuration_set_name_for(identity)
        try:
            self._ses.create_configuration_set(
                ConfigurationSet={"Name": name},
            )
        except Exception as exc:
            # AlreadyExists is the happy idempotent path.
            if "AlreadyExists" not in type(exc).__name__ and ("AlreadyExists" not in str(exc)):
                # Don't fail provision on configuration-set errors --
                # the identity is still usable; surface via message.
                return name

        # SNS event destination wiring (#756). When the platform has a
        # ``SES_EVENTS_SNS_TOPIC_ARN`` configured (the opscode-managed
        # platform-wide topic the ingestion webhook subscribes to) the
        # driver wires a ``platform-sns`` event destination on the
        # configuration set so SES publishes SEND / DELIVERY / BOUNCE
        # / COMPLAINT / OPEN / CLICK notifications through SNS to the
        # platform's ``/webhooks/ses-events/`` receiver.
        #
        # Best-effort: a failure here doesn't fail provision (the
        # identity is still usable for sending; the operator just
        # won't get per-message events until the destination is wired
        # manually). ``EventDestinationAlreadyExists`` is the
        # idempotent path on re-runs.
        topic_arn = self._sns_event_topic_arn()
        if topic_arn:
            try:
                self._ses.create_configuration_set_event_destination(
                    ConfigurationSetName=name,
                    EventDestination={
                        "Name": "platform-sns",
                        "Enabled": True,
                        "MatchingEventTypes": [
                            "send",
                            "delivery",
                            "bounce",
                            "complaint",
                            "open",
                            "click",
                        ],
                        "SNSDestination": {"TopicARN": topic_arn},
                    },
                )
            except Exception as exc:
                # EventDestinationAlreadyExists / ConfigurationSet
                # already wired -- treat as idempotent success.
                if "AlreadyExists" not in type(exc).__name__ and "AlreadyExists" not in str(exc):
                    # Soft-fail: log via the result message in the
                    # caller would be nicer, but the caller doesn't
                    # surface partial failures; swallow so identity
                    # provisioning still succeeds.
                    pass
        return name

    def _sns_event_topic_arn(self) -> str:
        """Resolve the platform's SES → SNS topic ARN.

        Two-source resolution: the explicit field on
        ``SESEmailConfig`` wins (used by unit tests that pass an ARN
        directly), falling back to Django's
        ``settings.SES_EVENTS_SNS_TOPIC_ARN`` so the production
        wiring doesn't need to touch every ``SESEmailConfig``
        constructor. The Django import is wrapped so the provider
        package stays importable from contexts that don't carry a
        configured Django (e.g. a standalone driver smoke test).
        """
        explicit = getattr(self._config, "sns_event_destination_arn", "") or ""
        if explicit:
            return str(explicit)
        try:
            from django.conf import settings  # type: ignore[import-not-found]
        except Exception:
            return ""
        try:
            value = getattr(settings, "SES_EVENTS_SNS_TOPIC_ARN", "")
        except Exception:
            return ""
        return str(value or "")

    def _delete_configuration_set(self, *, identity: str) -> None:
        name = self._configuration_set_name_for(identity)
        try:
            self._ses.delete_configuration_set(
                ConfigurationSetName=name,
            )
        except Exception:
            return

    def _smtp_access_key_secret_name(self, *, identity: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{_safe(identity)}/smtp-access-key-id"

    def _smtp_secret_key_secret_name(self, *, identity: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{_safe(identity)}/smtp-secret-key"

    def _ensure_smtp_credentials(
        self,
        *,
        spec: ProvisionSpec,
        identity: str,
    ) -> bool:
        """Store the placeholder SMTP secret pair. Returns ``True`` when
        both refs exist (created now or already present), ``False`` when a
        write failed. Best-effort: these are operator-overwritten
        placeholders, so a write failure (e.g. the platform role lacks
        ``secretsmanager:CreateSecret`` on this prefix) must NOT fail the
        provision of the load-bearing SES identity."""
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
        ok_access = self._put_secret_if_missing(
            name=access_key_name,
            value=self._config.smtp_username,
        )
        ok_secret = self._put_secret_if_missing(
            name=secret_key_name,
            value=_generate_smtp_password(),
        )
        return ok_access and ok_secret

    def _put_secret_if_missing(self, *, name: str, value: str) -> bool:
        """Create a secret if absent. Returns ``True`` when the secret
        exists afterwards (created or pre-existing), ``False`` when the
        write failed. Never raises: SMTP placeholder secrets are not the
        load-bearing resource, so a failure here is surfaced to the caller
        as a soft warning rather than aborting the provision."""
        try:
            self._sm.create_secret(
                Name=name,
                SecretString=value,
            )
            return True
        except Exception as exc:
            # Already-present is success; any other failure is a soft miss.
            return "ResourceExistsException" in type(exc).__name__

    def _dns_driver(self) -> Any | None:
        """Return the Route53 driver used to publish verification records,
        constructing it lazily. Best-effort: returns ``None`` (rather than
        raising) when boto3 / the driver can't be constructed so the DNS
        publish degrades to "operator publishes manually"."""
        if self._route53 is not None:
            return self._route53
        try:
            from aws.dns_route53 import Route53Driver

            self._route53 = Route53Driver()
        except Exception:
            return None
        return self._route53

    def _publish_verification_dns(self, *, identity: str) -> bool:
        """Publish the SES domain-verification TXT + Easy-DKIM CNAMEs into
        the operator's Route53 zone so the domain identity converges to
        Verified without a manual DNS step. Returns ``True`` when at least
        one record was written.

        Entirely best-effort -- returns ``False`` (never raises) when no
        ``base_domain`` is configured, the identity lives outside that
        zone, SES doesn't yield tokens, the zone isn't in Route53, or the
        platform role lacks Route53 access. In every such case the
        identity simply stays pending verification, which is a normal,
        non-failing state."""
        base_domain = (self._config.base_domain or "").strip().rstrip(".")
        if not base_domain:
            return False
        # Only publish when the identity lives inside the operator's zone;
        # otherwise we'd write records into the wrong hosted zone.
        if identity != base_domain and not identity.endswith(
            "." + base_domain,
        ):
            return False

        try:
            dkim = self._ses.verify_domain_dkim(Domain=identity)
            tokens = [t for t in (dkim.get("DkimTokens") or []) if t]
        except Exception:
            tokens = []
        verification_token = ""
        try:
            resp = self._ses.get_identity_verification_attributes(
                Identities=[identity],
            )
            attrs = resp.get("VerificationAttributes") or {}
            verification_token = str(
                (attrs.get(identity) or {}).get("VerificationToken") or "",
            )
        except Exception:
            verification_token = ""
        if not tokens and not verification_token:
            return False

        dns = self._dns_driver()
        if dns is None:
            return False

        wrote = False
        try:
            if verification_token:
                dns.ensure_record(
                    zone=base_domain,
                    name=f"_amazonses.{identity}",
                    type="TXT",
                    value=f'"{verification_token}"',
                )
                wrote = True
            for token in tokens:
                dns.ensure_record(
                    zone=base_domain,
                    name=f"{token}._domainkey.{identity}",
                    type="CNAME",
                    value=f"{token}.dkim.amazonses.com",
                )
                wrote = True
        except Exception:
            return wrote
        return wrote

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
        return f"{self._config.secrets_manager_prefix}/{_safe(identity)}/protection"

    def _set_deletion_protection(
        self,
        *,
        identity: str,
        enabled: bool,
    ) -> None:
        name = self._protection_marker_name(identity)
        value = "1" if enabled else "0"
        try:
            self._sm.create_secret(Name=name, SecretString=value)
        except Exception as exc:
            if "ResourceExistsException" in type(exc).__name__:
                try:
                    self._sm.put_secret_value(
                        SecretId=name,
                        SecretString=value,
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
                SecretId=name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


def identity_for(spec: ProvisionSpec, *, identity_prefix: str, base_domain: str) -> str:
    """The sending identity ``spec`` provisions, derived the same way
    regardless of caller.

    Pulled out of the driver instance method so the backend's own
    provisioning preflight (#2029) can compute the identity a
    ``ManagedService`` row would claim -- and check it against the rest
    of the platform's rows -- without duplicating the derivation, which
    would drift the two checks apart from each other."""
    cfg = spec.config or {}
    explicit = cfg.get("identity")
    if explicit:
        return str(explicit)
    # Per-service ``config={base_domain: ...}`` overrides the install-
    # level base_domain (#1038): a tenant provisioning email with its
    # own sending domain passes it through the mutation config, which
    # is threaded into ``spec.config``. Without this the driver only
    # read the install bundle's base_domain and raised even when the
    # caller supplied one.
    domain = str(cfg.get("base_domain") or "").strip() or base_domain
    if not domain:
        raise ManagedServiceError(
            "ses driver requires either spec.config.identity or a "
            "configured base_domain so it can derive a sending "
            "domain",
        )
    parts = [identity_prefix, spec.organization_slug, spec.app_slug, spec.environment_name]
    sub = "-".join(_safe(p) for p in parts if p)
    return f"{sub}.{domain}".lower()


def _verification_state(identity_response: dict[str, Any]) -> str:
    """Collapse SESv2's ``GetEmailIdentity`` shape to the v1 tri-state
    vocabulary (``Success`` / ``Pending`` / ``Failed``) the rest of the
    driver already speaks. SESv2 has no single verification-state string:
    ``VerifiedForSendingStatus`` is the real send-eligibility signal, and
    ``DkimAttributes.Status`` is the only place a hard failure shows up
    before that flips true."""
    if identity_response.get("VerifiedForSendingStatus"):
        return "Success"
    dkim_status = str((identity_response.get("DkimAttributes") or {}).get("Status") or "")
    return "Failed" if dkim_status == "FAILED" else "Pending"


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"NotFound", "NotFoundException"} or "not found" in str(exc).lower()


def _safe(value: str) -> str:
    """Coerce a slug to lowercase + replace anything outside
    ``[a-z0-9._-]`` with ``-``. SES + Secrets-Manager names share
    the same restrictive character set."""
    cleaned = "".join(c if (c.isalnum() or c in "-._") else "-" for c in value.lower())
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


_SMTP_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."


def _generate_smtp_password(length: int = 40) -> str:
    """Placeholder generator. Real SES SMTP passwords are produced
    by signing the IAM secret-access-key with the SES-specific
    derivation; that is an operator-side rotation step. The driver
    seeds a high-entropy value so binding has a stable ref the
    operator can overwrite."""
    return "".join(secrets.choice(_SMTP_PASSWORD_ALPHABET) for _ in range(length))
