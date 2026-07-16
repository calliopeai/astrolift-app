"""Tests for AWS SES observability + suppression driver (#629, #631, #632, #633, #634).

Covers the protocol surface in :class:`_sdk.email.EmailObservabilityDriver`:

* send-rate / quota / statistics reads
* account-send status (sandbox vs production, reputation derivation)
* identity verification + DKIM token surface
* DNS auth probe (DKIM via SES, SPF + DMARC via TXT lookup; the latter
  patched at the resolver edge so tests don't make network calls)
* suppression list view + manual add + manual remove

Cloud round-trips run through moto. DNS round-trips are stubbed at the
module-level ``lookup_txt`` so the suite stays hermetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from _sdk.email import (
    DnsCheckOutcome,
    SuppressionReason,
)
from aws.managed.email_ses import SESEmailConfig
from aws.managed.email_ses_obs import (
    AmazonSESObservabilityDriver,
    _check_dmarc,
    _check_spf,
)


class _FakeSesV2:
    """In-memory stand-in for the SES v2 account + suppression surface.

    ``moto`` doesn't implement ``list_suppressed_destinations`` /
    ``put_suppressed_destination`` / ``delete_suppressed_destination``
    / ``get_account``; rather than pin a feature-branched moto, mirror
    the small bit of state the driver touches inline. The shape of the
    response dicts mirrors what boto3 would return on a real SES call.
    """

    class _NotFoundError(Exception):
        pass

    def __init__(self) -> None:
        self._suppressed: dict[str, dict[str, Any]] = {}
        self._production_access = False

    # boto3 SES API keeps PascalCase argument names; noqa: N803 lines
    # below match the shape the driver passes through so the fake is
    # interchangeable with the real client.
    def list_suppressed_destinations(
        self,
        *,
        PageSize: int = 100,  # noqa: N803 — mirrors the boto3 kwarg
        **_: Any,
    ) -> dict[str, Any]:
        items = []
        for address, row in self._suppressed.items():
            items.append(
                {
                    "EmailAddress": address,
                    "Reason": row["Reason"],
                    "LastUpdateTime": row["LastUpdateTime"],
                }
            )
        return {"SuppressedDestinationSummaries": items[:PageSize]}

    def put_suppressed_destination(
        self,
        *,
        EmailAddress: str,  # noqa: N803 — mirrors the boto3 kwarg
        Reason: str,  # noqa: N803 — mirrors the boto3 kwarg
        **_: Any,
    ) -> dict[str, Any]:
        self._suppressed[EmailAddress] = {
            "Reason": Reason,
            "LastUpdateTime": datetime.now(UTC),
        }
        return {}

    def delete_suppressed_destination(
        self,
        *,
        EmailAddress: str,  # noqa: N803 — mirrors the boto3 kwarg
        **_: Any,
    ) -> dict[str, Any]:
        if EmailAddress not in self._suppressed:
            raise self._NotFoundError(f"NotFoundException: {EmailAddress} not on suppression list")
        del self._suppressed[EmailAddress]
        return {}

    def get_account(self) -> dict[str, Any]:
        return {
            "ProductionAccessEnabled": self._production_access,
            "SendingEnabled": True,
        }


@pytest.fixture
def aws_mock() -> Generator:
    from moto import mock_aws

    with mock_aws():
        ses = boto3.client("ses", region_name="us-east-1")
        sesv2 = _FakeSesV2()
        yield {"ses": ses, "sesv2": sesv2}


@pytest.fixture
def driver(aws_mock) -> AmazonSESObservabilityDriver:
    return AmazonSESObservabilityDriver(
        config=SESEmailConfig(
            region="us-east-1",
            base_domain="astrolift.test",
        ),
        ses_client=aws_mock["ses"],
        sesv2_client=aws_mock["sesv2"],
    )


# ---- send health -----------------------------------------------------


def test_get_send_quota_returns_floats(driver: AmazonSESObservabilityDriver) -> None:
    quota = driver.get_send_quota()
    assert quota.max_send_rate >= 0
    assert quota.max_24_hour_send >= 0
    assert quota.sent_last_24h >= 0


def test_get_send_statistics_oldest_first(
    driver: AmazonSESObservabilityDriver,
) -> None:
    # moto seeds an empty SendDataPoints list by default; the surface
    # contract is that we return a list (possibly empty) sorted
    # oldest-first.
    points = driver.get_send_statistics()
    assert isinstance(points, list)
    timestamps = [p.timestamp for p in points]
    assert timestamps == sorted(timestamps)


def test_account_send_status_default_sandbox(
    driver: AmazonSESObservabilityDriver,
) -> None:
    status = driver.get_account_send_status()
    # moto defaults a new account to sandbox + sending enabled.
    assert status.sending_enabled is True
    # ProductionAccessEnabled is False until an operator opts in.
    assert status.production_access is False
    # Reputation floor is 1000 send attempts; moto's clean state has
    # none, so reputation is None.
    assert status.reputation_score is None
    assert status.bounce_rate_pct is None
    assert status.complaint_rate_pct is None


def test_account_send_status_above_floor_computes_reputation(
    driver: AmazonSESObservabilityDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch get_send_statistics to return >=1000 attempts with a tiny
    # bounce rate; expect a high reputation_score and a populated
    # bounce_rate_pct.
    from _sdk.email import SendStatPoint

    fake = [
        SendStatPoint(
            timestamp=datetime.now(UTC),
            delivery_attempts=1500,
            bounces=3,
            complaints=1,
            rejects=0,
        ),
    ]
    monkeypatch.setattr(driver, "get_send_statistics", lambda: fake)
    status = driver.get_account_send_status()
    assert status.bounce_rate_pct is not None
    assert status.bounce_rate_pct == pytest.approx(0.2, abs=0.01)
    assert status.reputation_score is not None
    assert status.reputation_score > 0.8


# ---- identity --------------------------------------------------------


def test_identity_verification_details_for_unknown_domain(
    driver: AmazonSESObservabilityDriver,
) -> None:
    details = driver.get_identity_verification_details("ses.example.com")
    assert details.identity == "ses.example.com"
    assert details.is_domain is True
    # Unknown identity -> Pending state (the driver's default).
    assert details.status == "Pending"


def test_identity_verification_details_for_email_no_dkim(
    driver: AmazonSESObservabilityDriver,
    aws_mock,
) -> None:
    aws_mock["ses"].verify_email_identity(EmailAddress="ops@example.com")
    details = driver.get_identity_verification_details("ops@example.com")
    assert details.is_domain is False
    # DKIM tokens are domain-only.
    assert details.dkim_tokens == []


def test_identity_verification_details_for_domain_carries_dkim(
    driver: AmazonSESObservabilityDriver,
    aws_mock,
) -> None:
    aws_mock["ses"].verify_domain_identity(Domain="signed.example.com")
    aws_mock["ses"].verify_domain_dkim(Domain="signed.example.com")
    details = driver.get_identity_verification_details("signed.example.com")
    assert details.is_domain is True
    assert len(details.dkim_tokens) == 3
    for token in details.dkim_tokens:
        assert token.cname_host.endswith("._domainkey.signed.example.com")
        assert token.cname_target.endswith(".dkim.amazonses.com")


# ---- suppression list ------------------------------------------------


def test_list_suppression_empty(
    driver: AmazonSESObservabilityDriver,
) -> None:
    assert driver.list_suppression_entries() == []


def test_add_remove_suppression_round_trip(
    driver: AmazonSESObservabilityDriver,
) -> None:
    added = driver.add_suppression_entry(
        address="bouncy@example.com",
        reason=SuppressionReason.MANUAL,
        note="operator suppressed after support ticket",
    )
    assert added.address == "bouncy@example.com"
    # MANUAL maps onto the SES COMPLAINT bucket so the entry persists.
    assert added.reason == SuppressionReason.MANUAL

    listed = driver.list_suppression_entries()
    addresses = {e.address for e in listed}
    assert "bouncy@example.com" in addresses

    removed = driver.remove_suppression_entry(address="bouncy@example.com")
    assert removed is True

    listed_again = driver.list_suppression_entries()
    assert all(e.address != "bouncy@example.com" for e in listed_again)


def test_remove_nonexistent_address_returns_false(
    driver: AmazonSESObservabilityDriver,
) -> None:
    # Moto raises a ClientError(NotFoundException) for an unknown
    # address; the driver maps that to False without raising.
    result = driver.remove_suppression_entry(address="ghost@example.com")
    assert result is False


def test_suppression_reason_round_trip_bounce(
    driver: AmazonSESObservabilityDriver,
) -> None:
    driver.add_suppression_entry(
        address="hardbounce@example.com",
        reason=SuppressionReason.BOUNCE,
    )
    listed = driver.list_suppression_entries()
    match = next(
        (e for e in listed if e.address == "hardbounce@example.com"),
        None,
    )
    assert match is not None
    assert match.reason == SuppressionReason.BOUNCE


def test_list_suppression_clamps_page_size(
    driver: AmazonSESObservabilityDriver,
) -> None:
    # Sub-1 page size clamps to the 100 default; >1000 clamps to 1000.
    # We don't have 100 entries to test the lower clamp empirically,
    # but the call must not raise.
    assert driver.list_suppression_entries(page_size=0) == []
    assert driver.list_suppression_entries(page_size=9999) == []


# ---- DNS auth probe --------------------------------------------------


def test_spf_check_missing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: [],
    )
    check = _check_spf("example.com")
    assert check.outcome == DnsCheckOutcome.RED
    assert "no SPF" in check.message


def test_spf_check_authorizes_ses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: ["v=spf1 include:amazonses.com ~all"],
    )
    check = _check_spf("example.com")
    assert check.outcome == DnsCheckOutcome.GREEN


def test_spf_check_present_but_no_ses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: ["v=spf1 include:mailgun.org ~all"],
    )
    check = _check_spf("example.com")
    assert check.outcome == DnsCheckOutcome.YELLOW
    assert "amazonses" in check.message


def test_spf_check_multiple_records_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: [
            "v=spf1 include:amazonses.com ~all",
            "v=spf1 include:mailgun.org ~all",
        ],
    )
    check = _check_spf("example.com")
    assert check.outcome == DnsCheckOutcome.RED
    assert "multiple SPF" in check.message


def test_dmarc_check_reject_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: [
            "v=DMARC1; p=reject; rua=mailto:dmarc@example.com",
        ],
    )
    check = _check_dmarc("example.com")
    assert check.outcome == DnsCheckOutcome.GREEN
    assert "reject" in check.message


def test_dmarc_check_quarantine_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: [
            "v=DMARC1; p=quarantine; rua=mailto:dmarc@example.com",
        ],
    )
    check = _check_dmarc("example.com")
    assert check.outcome == DnsCheckOutcome.GREEN


def test_dmarc_check_none_is_yellow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: [
            "v=DMARC1; p=none; rua=mailto:dmarc@example.com",
        ],
    )
    check = _check_dmarc("example.com")
    assert check.outcome == DnsCheckOutcome.YELLOW


def test_dmarc_check_missing_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: [],
    )
    check = _check_dmarc("example.com")
    assert check.outcome == DnsCheckOutcome.RED


def test_dmarc_check_dns_failure_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from _sdk._dns_probe import DnsResolveError

    def _boom(*_args, **_kw):
        raise DnsResolveError("network partition")

    monkeypatch.setattr("aws.managed.email_ses_obs.lookup_txt", _boom)
    check = _check_dmarc("example.com")
    assert check.outcome == DnsCheckOutcome.UNKNOWN


def test_verify_dns_authentication_composite_status(
    driver: AmazonSESObservabilityDriver,
    aws_mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end composite: DKIM via SES + SPF/DMARC via patched TXT.

    Domain identity with verified DKIM, SPF authorizes SES, DMARC at
    p=reject — overall outcome should be GREEN.
    """
    aws_mock["ses"].verify_domain_identity(Domain="full.example.com")
    aws_mock["ses"].verify_domain_dkim(Domain="full.example.com")
    # moto sets DkimVerificationStatus=Success after verify_domain_dkim.

    def _txt(qname: str, **_kw):
        if qname == "full.example.com":
            return ["v=spf1 include:amazonses.com ~all"]
        if qname == "_dmarc.full.example.com":
            return ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"]
        return []

    monkeypatch.setattr("aws.managed.email_ses_obs.lookup_txt", _txt)

    status = driver.verify_dns_authentication("full.example.com")
    assert status.identity == "full.example.com"
    assert status.spf.outcome == DnsCheckOutcome.GREEN
    assert status.dmarc.outcome == DnsCheckOutcome.GREEN
    assert status.overall == DnsCheckOutcome.GREEN


def test_verify_dns_authentication_red_when_any_red(
    driver: AmazonSESObservabilityDriver,
    aws_mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aws_mock["ses"].verify_domain_identity(Domain="leaky.example.com")
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda *_args, **_kw: [],
    )
    status = driver.verify_dns_authentication("leaky.example.com")
    # SPF + DMARC missing -> both red; DKIM pending (no tokens) -> red.
    assert status.overall == DnsCheckOutcome.RED


def test_verify_dns_authentication_for_email_identity_dkim_is_yellow(
    driver: AmazonSESObservabilityDriver,
    aws_mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aws_mock["ses"].verify_email_identity(EmailAddress="ops@example.com")
    monkeypatch.setattr(
        "aws.managed.email_ses_obs.lookup_txt",
        lambda qname, **_kw: (
            ["v=spf1 include:amazonses.com ~all"]
            if qname == "example.com"
            else ["v=DMARC1; p=reject"]
            if qname == "_dmarc.example.com"
            else []
        ),
    )
    status = driver.verify_dns_authentication("ops@example.com")
    # DKIM can't be set on an email identity -> YELLOW; overall must
    # reflect that (YELLOW, since SPF + DMARC are green).
    assert status.dkim.outcome == DnsCheckOutcome.YELLOW
    assert status.overall == DnsCheckOutcome.YELLOW


# ---- template management (#635) -------------------------------------


def test_list_templates_empty(driver: AmazonSESObservabilityDriver) -> None:
    assert driver.list_templates() == []


def test_create_then_list_template_round_trip(
    driver: AmazonSESObservabilityDriver,
) -> None:
    created = driver.create_template(
        name="welcome-v1",
        subject="Welcome {{name}}",
        html_body="<p>Hi {{name}}</p>",
        text_body="Hi {{name}}",
    )
    assert created.name == "welcome-v1"
    assert created.subject == "Welcome {{name}}"

    listed = driver.list_templates()
    assert len(listed) == 1
    assert listed[0].name == "welcome-v1"
    assert listed[0].html_body == "<p>Hi {{name}}</p>"
    assert listed[0].text_body == "Hi {{name}}"


def test_create_template_duplicate_name_raises(
    driver: AmazonSESObservabilityDriver,
) -> None:
    from aws.managed._base import ManagedServiceError

    driver.create_template(
        name="dup",
        subject="s",
        html_body="h",
        text_body="t",
    )
    with pytest.raises(ManagedServiceError, match="already exists"):
        driver.create_template(
            name="dup",
            subject="s2",
            html_body="h2",
            text_body="t2",
        )


def test_get_template_returns_bodies(
    driver: AmazonSESObservabilityDriver,
) -> None:
    driver.create_template(
        name="invoice",
        subject="Your invoice",
        html_body="<p>Total: {{amount}}</p>",
        text_body="Total: {{amount}}",
    )
    tmpl = driver.get_template(name="invoice")
    assert tmpl.name == "invoice"
    assert tmpl.subject == "Your invoice"
    assert tmpl.html_body == "<p>Total: {{amount}}</p>"
    assert tmpl.text_body == "Total: {{amount}}"


def test_get_template_missing_raises_managed_service_error(
    driver: AmazonSESObservabilityDriver,
) -> None:
    from aws.managed._base import ManagedServiceError

    with pytest.raises(ManagedServiceError, match="not found"):
        driver.get_template(name="ghost")


def test_update_template_replaces_body(
    driver: AmazonSESObservabilityDriver,
) -> None:
    driver.create_template(
        name="alert",
        subject="v1",
        html_body="<p>v1</p>",
        text_body="v1",
    )
    updated = driver.update_template(
        name="alert",
        subject="v2",
        html_body="<p>v2</p>",
        text_body="v2",
    )
    assert updated.subject == "v2"
    fetched = driver.get_template(name="alert")
    assert fetched.subject == "v2"
    assert fetched.html_body == "<p>v2</p>"


def test_update_template_missing_raises(
    driver: AmazonSESObservabilityDriver,
) -> None:
    from aws.managed._base import ManagedServiceError

    with pytest.raises(ManagedServiceError, match="not found"):
        driver.update_template(
            name="never-there",
            subject="s",
            html_body="h",
            text_body="t",
        )


def test_delete_template_returns_true_when_present(
    driver: AmazonSESObservabilityDriver,
) -> None:
    driver.create_template(
        name="trash",
        subject="s",
        html_body="h",
        text_body="t",
    )
    assert driver.delete_template(name="trash") is True
    assert driver.list_templates() == []


def test_delete_template_returns_false_when_missing(
    driver: AmazonSESObservabilityDriver,
) -> None:
    # moto raises KeyError on delete-missing; the driver's helper must
    # map that to False so the operator's idempotent retry doesn't 500.
    assert driver.delete_template(name="never-existed") is False


# ---- per-template send statistics (#628) ----------------------------


class _FakeCloudWatch:
    """In-memory CloudWatch fake that returns canned datapoints for the
    four SES per-template metrics.

    Mirrors the boto3 ``get_metric_statistics`` response shape. Tests
    pre-populate ``self.data[metric]`` with ``{timestamp: sum}`` so
    each call returns the requested metric's series."""

    def __init__(self, data: dict[str, dict[datetime, int]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._data = data

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        metric = kwargs["MetricName"]
        series = self._data.get(metric, {})
        datapoints = [{"Timestamp": ts, "Sum": float(value)} for ts, value in series.items()]
        return {"Datapoints": datapoints}


@pytest.fixture
def cloudwatch_driver(aws_mock) -> AmazonSESObservabilityDriver:
    fake_cw = _FakeCloudWatch({})
    drv = AmazonSESObservabilityDriver(
        config=SESEmailConfig(
            region="us-east-1",
            base_domain="astrolift.test",
        ),
        ses_client=aws_mock["ses"],
        sesv2_client=aws_mock["sesv2"],
        cloudwatch_client=fake_cw,
    )
    drv._fake_cw = fake_cw  # type: ignore[attr-defined]
    return drv


def test_template_send_statistics_empty_when_no_data(
    cloudwatch_driver: AmazonSESObservabilityDriver,
) -> None:
    points = cloudwatch_driver.get_template_send_statistics(name="welcome-v1")
    assert points == []


def test_template_send_statistics_merges_four_metrics(
    cloudwatch_driver: AmazonSESObservabilityDriver,
) -> None:
    ts1 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    ts2 = datetime(2026, 1, 1, 10, 15, tzinfo=UTC)
    cloudwatch_driver._fake_cw._data = {  # type: ignore[attr-defined]
        "Send": {ts1: 10, ts2: 20},
        "Delivery": {ts1: 9, ts2: 19},
        "Bounce": {ts2: 1},
        "Complaint": {},
    }
    points = cloudwatch_driver.get_template_send_statistics(
        name="welcome-v1",
        days=7,
    )
    assert len(points) == 2
    # Oldest-first per the protocol contract.
    assert points[0].timestamp == ts1
    assert points[1].timestamp == ts2
    assert points[0].sends == 10
    assert points[0].deliveries == 9
    assert points[0].bounces == 0
    assert points[0].complaints == 0
    assert points[1].sends == 20
    assert points[1].bounces == 1


def test_template_send_statistics_uses_template_dimension(
    cloudwatch_driver: AmazonSESObservabilityDriver,
) -> None:
    cloudwatch_driver.get_template_send_statistics(name="welcome-v1")
    calls = cloudwatch_driver._fake_cw.calls  # type: ignore[attr-defined]
    # Four metric calls (Send, Delivery, Bounce, Complaint).
    assert len(calls) == 4
    metrics = {c["MetricName"] for c in calls}
    assert metrics == {"Send", "Delivery", "Bounce", "Complaint"}
    for call in calls:
        assert call["Namespace"] == "AWS/SES"
        assert call["Period"] == 900
        assert call["Statistics"] == ["Sum"]
        dims = call["Dimensions"]
        assert dims == [
            {"Name": "ses2:TemplateName", "Value": "welcome-v1"},
        ]


def test_template_send_statistics_swallow_cloudwatch_failure(
    cloudwatch_driver: AmazonSESObservabilityDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("cloudwatch throttled")

    fake_cw = cloudwatch_driver._fake_cw  # type: ignore[attr-defined]
    monkeypatch.setattr(fake_cw, "get_metric_statistics", _boom)
    # Cloud-side failure -> empty list, not a raised exception. The
    # resolver layer renders "no metrics yet" rather than a 500.
    assert cloudwatch_driver.get_template_send_statistics(name="t") == []


def test_template_send_statistics_clamps_days(
    cloudwatch_driver: AmazonSESObservabilityDriver,
) -> None:
    # Out-of-range days collapse to the [1, 14] range; the test
    # confirms the call doesn't raise on either bound.
    assert cloudwatch_driver.get_template_send_statistics(name="t", days=0) == []
    assert cloudwatch_driver.get_template_send_statistics(name="t", days=999) == []
