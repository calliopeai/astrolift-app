"""AWS SES email observability + suppression driver (#629, #631, #632, #633, #634).

Implements :class:`_sdk.email.EmailObservabilityDriver` against the SES
v1 + v2 APIs. The v1 client (``boto3.client('ses')``) carries the
``get_send_quota`` / ``get_send_statistics`` / ``get_identity_dkim_attributes``
surface; v2 (``boto3.client('sesv2')``) owns the account-level
suppression list and the production-access flag the v1 control plane
deprecated.

Split rationale:

* :mod:`aws.managed.email_ses` keeps the lifecycle contract
  (provision / update / deprovision / status / binding) — the same
  shape every managed-service driver implements. Lifting the
  observability surface into this sibling module keeps the lifecycle
  driver tight and lets the observability driver evolve without
  rebinding the manifest schema.
* Both drivers share the same ``SESEmailConfig`` instance, so the
  configuration set name + secrets prefix line up across reads and
  writes.

Every public method is instrumented with ``@driver_op`` so the
operations pipeline sees structured logs + Prometheus metrics + OTEL
spans + Temporal heartbeats + audit emission. Suppression mutations
carry ``audit=True`` + ``sensitive_kind`` so the audit row reflects the
sensitive-op semantics the resolver promises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from _sdk import (
    AccountSendStatus,
    DkimToken,
    DnsAuthCheck,
    DnsAuthStatus,
    DnsCheckOutcome,
    EmailObservabilityDriver,
    EmailTemplate,
    IdentityVerification,
    SendQuota,
    SendStatPoint,
    SuppressionEntry,
    SuppressionReason,
    TemplateSendStatPoint,
    driver_op,
)
from _sdk._dns_probe import DnsResolveError, lookup_txt
from aws.managed._base import ManagedServiceError

if TYPE_CHECKING:
    from aws.managed.email_ses import SESEmailConfig

log = logging.getLogger("astrolift.providers.aws.email_ses_obs")


# SES v2 takes one of these strings on the put_suppressed_destination
# call; the platform's SuppressionReason maps cleanly onto BOUNCE +
# COMPLAINT. MANUAL is a platform-level distinction; SES requires us
# to pick BOUNCE or COMPLAINT, and we pick COMPLAINT so the recipient
# stays suppressed across re-provisions (BOUNCE entries can age out).
_REASON_TO_SES: dict[SuppressionReason, str] = {
    SuppressionReason.BOUNCE: "BOUNCE",
    SuppressionReason.COMPLAINT: "COMPLAINT",
    SuppressionReason.MANUAL: "COMPLAINT",
}


_SES_TO_REASON: dict[str, SuppressionReason] = {
    "BOUNCE": SuppressionReason.BOUNCE,
    "COMPLAINT": SuppressionReason.COMPLAINT,
}


# Production-access for an account is communicated via the v2
# ``get_account`` response; SES doesn't expose a single ``in_sandbox``
# boolean — operators read ``production_access_status`` and the array
# of supported sending-modes. The platform collapses both to a single
# bool so the UI tile stays one cell.
_PRODUCTION_GRANTED_STATUSES = frozenset({"GRANTED", "ENABLED"})


@dataclass(frozen=True)
class _DkimAttributes:
    """Shape returned by ``get_identity_dkim_attributes`` for one identity."""

    enabled: bool
    tokens: list[str]
    verification_status: str


class AmazonSESObservabilityDriver(EmailObservabilityDriver):
    """SES v1 + v2 read surface + suppression mutations.

    Constructor accepts pre-built clients (used by tests) or builds
    them from the region carried on ``SESEmailConfig``. The lifecycle
    driver and the observability driver SHOULD share one config + one
    pair of clients per process; the resolver caches the pair.
    """

    def __init__(
        self,
        *,
        config: SESEmailConfig,
        ses_client: Any | None = None,
        sesv2_client: Any | None = None,
        cloudwatch_client: Any | None = None,
    ) -> None:
        self._config = config
        if ses_client is not None:
            self._ses = ses_client
        else:
            import boto3

            self._ses = boto3.client("ses", region_name=config.region)
        if sesv2_client is not None:
            self._sesv2 = sesv2_client
        else:
            import boto3

            self._sesv2 = boto3.client("sesv2", region_name=config.region)
        # CloudWatch client is lazy — only required by the per-template
        # send-statistics path (#628). Tests inject a fake; production
        # paths share boto3's client cache by re-creating it when None.
        self._cloudwatch = cloudwatch_client

    # ---- send health ------------------------------------------------

    @driver_op(driver="email", cloud="aws")
    def get_send_quota(self) -> SendQuota:
        resp = self._ses.get_send_quota()
        return SendQuota(
            max_send_rate=float(resp.get("MaxSendRate") or 0.0),
            max_24_hour_send=float(resp.get("Max24HourSend") or 0.0),
            sent_last_24h=float(resp.get("SentLast24Hours") or 0.0),
        )

    @driver_op(driver="email", cloud="aws")
    def get_send_statistics(self) -> list[SendStatPoint]:
        resp = self._ses.get_send_statistics()
        points: list[SendStatPoint] = []
        for raw in resp.get("SendDataPoints") or []:
            ts = raw.get("Timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    ts = datetime.now(UTC)
            elif ts is None:
                ts = datetime.now(UTC)
            points.append(
                SendStatPoint(
                    timestamp=ts,
                    delivery_attempts=int(raw.get("DeliveryAttempts") or 0),
                    bounces=int(raw.get("Bounces") or 0),
                    complaints=int(raw.get("Complaints") or 0),
                    rejects=int(raw.get("Rejects") or 0),
                )
            )
        # SES returns points in arbitrary order; oldest-first is the
        # contract the UI tile + the SDK protocol promise.
        points.sort(key=lambda p: p.timestamp)
        return points

    @driver_op(driver="email", cloud="aws")
    def get_account_send_status(self) -> AccountSendStatus:
        sending_enabled = True
        try:
            sending_enabled = bool(
                self._ses.get_account_sending_enabled().get("Enabled", True)
            )
        except Exception as exc:
            log.debug("get_account_sending_enabled fallback: %s", exc)

        production_access = False
        try:
            account = self._sesv2.get_account()
            pa = account.get("ProductionAccessEnabled")
            if pa is None:
                # Some SES regions expose a string field instead of a bool.
                pa_status = (account.get("Details") or {}).get(
                    "ReviewDetails", {}
                ).get("Status")
                production_access = pa_status in _PRODUCTION_GRANTED_STATUSES
            else:
                production_access = bool(pa)
        except Exception as exc:
            log.debug("sesv2.get_account fallback: %s", exc)

        # Reputation: derived from the last-14-day send statistics so
        # the platform doesn't depend on the (non-public) SES reputation
        # endpoint. Floor of 1000 attempts before a number is meaningful;
        # below the floor we return None.
        stats = self.get_send_statistics()
        total_attempts = sum(p.delivery_attempts for p in stats)
        bounce_rate = complaint_rate = None
        reputation = None
        if total_attempts >= 1000:
            bounces = sum(p.bounces for p in stats)
            complaints = sum(p.complaints for p in stats)
            bounce_rate = (bounces / total_attempts) * 100
            complaint_rate = (complaints / total_attempts) * 100
            # Reputation: 1.0 minus a weighted penalty. Complaints
            # weigh 10x bounces because SES enforces a complaint
            # threshold ~20x stricter (0.5% vs 10%).
            penalty = (bounces + complaints * 10) / total_attempts
            reputation = max(0.0, min(1.0, 1.0 - penalty * 10))

        return AccountSendStatus(
            sending_enabled=sending_enabled,
            production_access=production_access,
            reputation_score=reputation,
            bounce_rate_pct=bounce_rate,
            complaint_rate_pct=complaint_rate,
        )

    # ---- identity / dns auth ---------------------------------------

    @driver_op(driver="email", cloud="aws")
    def get_identity_verification_details(
        self, identity: str
    ) -> IdentityVerification:
        is_domain = "@" not in identity

        # Verification status
        v_resp = self._ses.get_identity_verification_attributes(
            Identities=[identity],
        )
        attrs = v_resp.get("VerificationAttributes") or {}
        status = "Pending"
        verification_token = ""
        if identity in attrs:
            status = attrs[identity].get("VerificationStatus") or "Pending"
            verification_token = attrs[identity].get("VerificationToken") or ""

        dkim_tokens: list[DkimToken] = []
        if is_domain:
            dkim = self._fetch_dkim(identity)
            for raw in dkim.tokens:
                dkim_tokens.append(
                    DkimToken(
                        token=raw,
                        cname_host=f"{raw}._domainkey.{identity}",
                        cname_target=f"{raw}.dkim.amazonses.com",
                    )
                )

        return IdentityVerification(
            identity=identity,
            is_domain=is_domain,
            status=status,
            verification_token=verification_token,
            dkim_tokens=dkim_tokens,
        )

    @driver_op(driver="email", cloud="aws")
    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        is_domain = "@" not in identity
        # Lift the bare domain off an email-form identity so we can probe
        # the zone that hosts the SPF/DMARC records.
        domain = identity.split("@", 1)[-1] if not is_domain else identity

        return DnsAuthStatus(
            identity=identity,
            checked_at=datetime.now(UTC),
            dkim=self._check_dkim(domain, is_domain=is_domain),
            spf=_check_spf(domain),
            dmarc=_check_dmarc(domain),
        )

    # ---- suppression list ------------------------------------------

    @driver_op(driver="email", cloud="aws")
    def list_suppression_entries(
        self,
        *,
        page_size: int = 100,
    ) -> list[SuppressionEntry]:
        if page_size < 1:
            page_size = 100
        if page_size > 1000:
            page_size = 1000
        resp = self._sesv2.list_suppressed_destinations(PageSize=page_size)
        out: list[SuppressionEntry] = []
        for raw in resp.get("SuppressedDestinationSummaries") or []:
            address = raw.get("EmailAddress") or ""
            if not address:
                continue
            reason_raw = raw.get("Reason") or "BOUNCE"
            reason = _SES_TO_REASON.get(
                reason_raw.upper(), SuppressionReason.MANUAL
            )
            suppressed_at = raw.get("LastUpdateTime")
            if isinstance(suppressed_at, str):
                try:
                    suppressed_at = datetime.fromisoformat(
                        suppressed_at.replace("Z", "+00:00")
                    )
                except ValueError:
                    suppressed_at = datetime.now(UTC)
            elif suppressed_at is None:
                suppressed_at = datetime.now(UTC)
            out.append(
                SuppressionEntry(
                    address=address,
                    reason=reason,
                    suppressed_at=suppressed_at,
                )
            )
        return out

    @driver_op(
        driver="email",
        cloud="aws",
        audit=True,
        sensitive_kind="email.suppression.add",
        redact_args=("note",),
    )
    def add_suppression_entry(
        self,
        *,
        address: str,
        reason: SuppressionReason,
        note: str = "",
    ) -> SuppressionEntry:
        # SES requires BOUNCE or COMPLAINT; we map MANUAL onto COMPLAINT
        # because it carries longer-lived suppression semantics on the
        # SES side (bounce entries age out, complaint entries persist).
        ses_reason = _REASON_TO_SES[reason]
        self._sesv2.put_suppressed_destination(
            EmailAddress=address,
            Reason=ses_reason,
        )
        # SES doesn't echo the entry back on put; return what we wrote
        # so the resolver can render it without a follow-up list call.
        return SuppressionEntry(
            address=address,
            reason=reason,
            suppressed_at=datetime.now(UTC),
            detail=note or "manual add",
        )

    @driver_op(
        driver="email",
        cloud="aws",
        audit=True,
        sensitive_kind="email.suppression.remove",
    )
    def remove_suppression_entry(self, *, address: str) -> bool:
        try:
            self._sesv2.delete_suppressed_destination(EmailAddress=address)
            return True
        except Exception as exc:
            # ClientError carries the SES code on .response['Error']['Code'];
            # everything else (test fakes, network errors) falls through to
            # the substring guard. Both paths converge on False so a
            # repeated remove is idempotent.
            code = ""
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                code = (response.get("Error") or {}).get("Code", "")
            msg = str(exc).lower()
            if code == "NotFoundException" or (
                "notfound" in msg or "not found" in msg
            ):
                return False
            raise

    # ---- template management (#635) --------------------------------

    @driver_op(driver="email", cloud="aws")
    def list_templates(self) -> list[EmailTemplate]:
        """Walk the SES ``list_templates`` page (capped at 100 per call)
        and hydrate each row with its body via ``get_template``.

        SES splits list/get cleanly: ``list_templates`` returns
        ``{Name, CreatedTimestamp}`` metadata only; bodies require a
        per-template ``get_template`` round-trip. The hydration cost
        is bounded by the 100-row page cap and the typical operator
        template count (<20), so the platform absorbs it inline to
        avoid a two-call dance on the frontend. A get_template that
        raises (template was deleted between list + get) is skipped
        with a debug log; the rest of the page still renders.
        """
        response = self._ses.list_templates(MaxItems=100)
        templates: list[EmailTemplate] = []
        for meta in response.get("TemplatesMetadata") or []:
            name = meta.get("Name") or ""
            if not name:
                continue
            created_at = meta.get("CreatedTimestamp")
            try:
                detail = self._ses.get_template(TemplateName=name)
            except Exception as exc:
                log.debug("get_template(%s) skipped during list: %s", name, exc)
                continue
            tmpl = detail.get("Template") or {}
            templates.append(
                EmailTemplate(
                    name=name,
                    subject=str(tmpl.get("SubjectPart") or ""),
                    html_body=str(tmpl.get("HtmlPart") or ""),
                    text_body=str(tmpl.get("TextPart") or ""),
                    created_at=created_at,
                )
            )
        return templates

    @driver_op(driver="email", cloud="aws")
    def get_template(self, *, name: str) -> EmailTemplate:
        try:
            response = self._ses.get_template(TemplateName=name)
        except Exception as exc:
            if _is_template_not_found(exc):
                raise ManagedServiceError(
                    f"template {name!r} not found",
                ) from exc
            raise
        tmpl = response.get("Template") or {}
        return EmailTemplate(
            name=name,
            subject=str(tmpl.get("SubjectPart") or ""),
            html_body=str(tmpl.get("HtmlPart") or ""),
            text_body=str(tmpl.get("TextPart") or ""),
        )

    @driver_op(
        driver="email",
        cloud="aws",
        audit=True,
        sensitive_kind="email.template.create",
    )
    def create_template(
        self,
        *,
        name: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> EmailTemplate:
        try:
            self._ses.create_template(
                Template={
                    "TemplateName": name,
                    "SubjectPart": subject,
                    "HtmlPart": html_body,
                    "TextPart": text_body,
                },
            )
        except Exception as exc:
            if _is_template_already_exists(exc):
                raise ManagedServiceError(
                    f"template {name!r} already exists",
                ) from exc
            raise
        return EmailTemplate(
            name=name,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )

    @driver_op(
        driver="email",
        cloud="aws",
        audit=True,
        sensitive_kind="email.template.update",
    )
    def update_template(
        self,
        *,
        name: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> EmailTemplate:
        try:
            self._ses.update_template(
                Template={
                    "TemplateName": name,
                    "SubjectPart": subject,
                    "HtmlPart": html_body,
                    "TextPart": text_body,
                },
            )
        except Exception as exc:
            if _is_template_not_found(exc):
                raise ManagedServiceError(
                    f"template {name!r} not found",
                ) from exc
            raise
        return EmailTemplate(
            name=name,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )

    @driver_op(
        driver="email",
        cloud="aws",
        audit=True,
        sensitive_kind="email.template.delete",
    )
    def delete_template(self, *, name: str) -> bool:
        try:
            self._ses.delete_template(TemplateName=name)
            return True
        except Exception as exc:
            if _is_template_not_found(exc):
                return False
            raise

    @driver_op(driver="email", cloud="aws")
    def get_template_send_statistics(
        self,
        *,
        name: str,
        days: int = 14,
    ) -> list[TemplateSendStatPoint]:
        """Fan four CloudWatch ``GetMetricStatistics`` calls (Send,
        Delivery, Bounce, Complaint) against the ``ses2:TemplateName``
        dimension and merge their datapoints on the timestamp axis.

        CloudWatch returns one metric per call; the four-call dance is
        the only first-party path to per-template counters today. SES
        publishes these only when the configuration set has the
        ``cloudwatch`` event destination wired and the SendTemplated*
        path tagged the template name as a dimension; absent that we
        return ``[]`` rather than synthesising zeros so the UI can
        render an "no metrics yet" hint.
        """
        cw = self._cw_client()
        if cw is None:
            return []
        # CloudWatch caps GetMetricStatistics at 1440 datapoints per
        # call. At 15-minute granularity that's 15 days of headroom,
        # so a 14-day default fits comfortably in one call.
        bounded_days = max(1, min(int(days or 14), 14))
        end = datetime.now(UTC)
        start = end - _td_days(bounded_days)
        try:
            sends = _fetch_template_metric(
                cw,
                name=name,
                metric="Send",
                start=start,
                end=end,
            )
            deliveries = _fetch_template_metric(
                cw,
                name=name,
                metric="Delivery",
                start=start,
                end=end,
            )
            bounces = _fetch_template_metric(
                cw,
                name=name,
                metric="Bounce",
                start=start,
                end=end,
            )
            complaints = _fetch_template_metric(
                cw,
                name=name,
                metric="Complaint",
                start=start,
                end=end,
            )
        except Exception as exc:
            log.debug("cloudwatch GetMetricStatistics failed: %s", exc)
            return []
        timestamps = sorted(set(sends) | set(deliveries) | set(bounces) | set(complaints))
        return [
            TemplateSendStatPoint(
                timestamp=ts,
                sends=sends.get(ts, 0),
                deliveries=deliveries.get(ts, 0),
                bounces=bounces.get(ts, 0),
                complaints=complaints.get(ts, 0),
            )
            for ts in timestamps
        ]

    def _cw_client(self) -> Any | None:
        if self._cloudwatch is not None:
            return self._cloudwatch
        try:
            import boto3

            self._cloudwatch = boto3.client(
                "cloudwatch",
                region_name=self._config.region,
            )
        except Exception as exc:  # pragma: no cover — boto3 import path
            log.debug("cloudwatch client init failed: %s", exc)
            return None
        return self._cloudwatch

    # ---- internals --------------------------------------------------

    def _fetch_dkim(self, identity: str) -> _DkimAttributes:
        resp = self._ses.get_identity_dkim_attributes(Identities=[identity])
        attrs = (resp.get("DkimAttributes") or {}).get(identity) or {}
        return _DkimAttributes(
            enabled=bool(attrs.get("DkimEnabled", False)),
            tokens=list(attrs.get("DkimTokens") or []),
            verification_status=str(
                attrs.get("DkimVerificationStatus") or "Pending"
            ),
        )

    def _check_dkim(self, domain: str, *, is_domain: bool) -> DnsAuthCheck:
        # SES only ships DKIM for domain identities; email-only
        # identities (single-mailbox verifies) can't sign.
        if not is_domain:
            return DnsAuthCheck(
                protocol="DKIM",
                outcome=DnsCheckOutcome.YELLOW,
                message=(
                    "identity is a single email address; DKIM signing "
                    "requires a domain identity"
                ),
            )
        try:
            dkim = self._fetch_dkim(domain)
        except Exception as exc:
            return DnsAuthCheck(
                protocol="DKIM",
                outcome=DnsCheckOutcome.UNKNOWN,
                message=f"get_identity_dkim_attributes failed: {exc}",
            )

        status = dkim.verification_status
        if status == "Success" and dkim.enabled and dkim.tokens:
            return DnsAuthCheck(
                protocol="DKIM",
                outcome=DnsCheckOutcome.GREEN,
                records=list(dkim.tokens),
                message="DKIM CNAMEs verified and signing enabled",
            )
        if status == "Pending":
            return DnsAuthCheck(
                protocol="DKIM",
                outcome=DnsCheckOutcome.RED,
                records=list(dkim.tokens),
                message=(
                    "DKIM verification pending — publish the 3 CNAMEs in "
                    "DNS and wait for SES to detect them"
                ),
            )
        if status == "Failed":
            return DnsAuthCheck(
                protocol="DKIM",
                outcome=DnsCheckOutcome.RED,
                records=list(dkim.tokens),
                message="DKIM verification failed — re-trigger from SES",
            )
        return DnsAuthCheck(
            protocol="DKIM",
            outcome=DnsCheckOutcome.YELLOW,
            records=list(dkim.tokens),
            message=f"DKIM in state {status!r} — review SES console",
        )


# ----- module-level helpers --------------------------------------------


def _check_spf(domain: str) -> DnsAuthCheck:
    """Probe the apex TXT record for an SPF entry that includes SES.

    SPF lives in a TXT at the apex with content starting ``v=spf1``. To
    authorize SES the record must include ``include:amazonses.com``;
    anything else is yellow (records exist but don't authorize us).
    """
    try:
        records = lookup_txt(domain)
    except DnsResolveError as exc:
        return DnsAuthCheck(
            protocol="SPF",
            outcome=DnsCheckOutcome.UNKNOWN,
            message=f"DNS probe failed: {exc}",
        )

    spf_records = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf_records:
        return DnsAuthCheck(
            protocol="SPF",
            outcome=DnsCheckOutcome.RED,
            message=(
                f"no SPF (v=spf1) record at {domain} — add "
                "'v=spf1 include:amazonses.com ~all' to authorize SES"
            ),
        )
    if len(spf_records) > 1:
        return DnsAuthCheck(
            protocol="SPF",
            outcome=DnsCheckOutcome.RED,
            records=spf_records,
            message=(
                f"multiple SPF records at {domain} — RFC 7208 forbids "
                "this; consolidate into one TXT"
            ),
        )
    record = spf_records[0]
    if "include:amazonses.com" not in record.lower() and (
        "include:_spf.amazonses.com" not in record.lower()
    ):
        return DnsAuthCheck(
            protocol="SPF",
            outcome=DnsCheckOutcome.YELLOW,
            records=[record],
            message=(
                "SPF present but doesn't include amazonses.com — "
                "SES sends will fail SPF alignment"
            ),
        )
    return DnsAuthCheck(
        protocol="SPF",
        outcome=DnsCheckOutcome.GREEN,
        records=[record],
        message="SPF authorizes SES",
    )


def _check_dmarc(domain: str) -> DnsAuthCheck:
    """Probe ``_dmarc.<domain>`` for a DMARC policy.

    DMARC policies grade as:
    * ``p=reject`` -> green (strict alignment enforced)
    * ``p=quarantine`` -> green (mail flagged but enforced)
    * ``p=none`` -> yellow (monitor-only; spoofing isn't blocked)
    * missing -> red (no DMARC policy at all)
    """
    qname = f"_dmarc.{domain}"
    try:
        records = lookup_txt(qname)
    except DnsResolveError as exc:
        return DnsAuthCheck(
            protocol="DMARC",
            outcome=DnsCheckOutcome.UNKNOWN,
            message=f"DNS probe failed: {exc}",
        )

    dmarc_records = [r for r in records if r.lower().startswith("v=dmarc1")]
    if not dmarc_records:
        return DnsAuthCheck(
            protocol="DMARC",
            outcome=DnsCheckOutcome.RED,
            message=(
                f"no DMARC record at {qname} — add "
                "'v=DMARC1; p=none; rua=mailto:...' to start monitoring"
            ),
        )
    record = dmarc_records[0]
    lower = record.lower()
    if "p=reject" in lower:
        return DnsAuthCheck(
            protocol="DMARC",
            outcome=DnsCheckOutcome.GREEN,
            records=[record],
            message="DMARC policy=reject (strict enforcement)",
        )
    if "p=quarantine" in lower:
        return DnsAuthCheck(
            protocol="DMARC",
            outcome=DnsCheckOutcome.GREEN,
            records=[record],
            message="DMARC policy=quarantine (enforced)",
        )
    if "p=none" in lower:
        return DnsAuthCheck(
            protocol="DMARC",
            outcome=DnsCheckOutcome.YELLOW,
            records=[record],
            message=(
                "DMARC policy=none (monitor-only) — escalate to "
                "quarantine or reject once you trust the reports"
            ),
        )
    return DnsAuthCheck(
        protocol="DMARC",
        outcome=DnsCheckOutcome.YELLOW,
        records=[record],
        message="DMARC present but policy unclear — review the record",
    )


def _td_days(days: int):  # type: ignore[no-untyped-def]
    """Module-private import shim — keeps the ``timedelta`` import next
    to the metrics helpers so the lifecycle-side imports stay quiet."""
    from datetime import timedelta

    return timedelta(days=int(days))


def _fetch_template_metric(
    cw: Any,
    *,
    name: str,
    metric: str,
    start: datetime,
    end: datetime,
) -> dict[datetime, int]:
    """One CloudWatch GetMetricStatistics call → timestamp-indexed sums.

    SES publishes per-template counters on the ``ses2:TemplateName``
    dimension of the ``AWS/SES`` namespace (the older ``ses:`` prefix
    is deprecated for new event destinations; the v2 ``ses2:`` form is
    what SendTemplatedEmail v2 emits). Statistics=["Sum"] aggregates
    each 15-minute bucket; the response is unordered so the caller
    merges + sorts across the four metric calls."""
    response = cw.get_metric_statistics(
        Namespace="AWS/SES",
        MetricName=metric,
        Dimensions=[{"Name": "ses2:TemplateName", "Value": name}],
        StartTime=start,
        EndTime=end,
        Period=900,
        Statistics=["Sum"],
    )
    out: dict[datetime, int] = {}
    for dp in response.get("Datapoints") or []:
        ts = dp.get("Timestamp")
        if not isinstance(ts, datetime):
            continue
        out[ts] = int(dp.get("Sum") or 0)
    return out


# SES exposes template-not-found as
# ``TemplateDoesNotExistException`` on the per-template fetch and
# ``AlreadyExistsException`` on create. Both come through as
# ``ClientError`` with an ``Error.Code`` field; tests + moto sometimes
# raise a custom exception type instead. Match on both shapes so the
# driver behaves the same against real SES + test fakes.
def _exception_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return type(exc).__name__


def _is_template_not_found(exc: BaseException) -> bool:
    code = _exception_code(exc)
    if "TemplateDoesNotExist" in code:
        return True
    if "NotFound" in code:
        return True
    # moto raises a bare KeyError(name) from delete_template when the
    # template doesn't exist; recognize that path so the driver's
    # delete returns False instead of bubbling the error.
    if isinstance(exc, KeyError):
        return True
    return "does not exist" in str(exc).lower()


def _is_template_already_exists(exc: BaseException) -> bool:
    code = _exception_code(exc)
    if "AlreadyExists" in code:
        return True
    return "already exists" in str(exc).lower()
