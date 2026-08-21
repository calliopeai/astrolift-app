"""Tests for the SES bounce-rate / complaint-rate predicate kinds (#757).

The handlers under test pull from EmailObservabilityDriver via the
``astrolift_services.email_observability`` registry. We stub the driver
via ``register_driver_override`` so the suite stays hermetic — no boto3,
no DNS — same seam the email-observability resolver tests use.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from _sdk import UnsupportedOperationError
from _sdk.email import (
    AccountSendStatus,
    DnsAuthStatus,
    EmailObservabilityDriver,
    IdentityVerification,
    SendQuota,
    SendStatPoint,
    SuppressionEntry,
    SuppressionReason,
)

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.alert_evaluators import (
    KIND_HANDLERS,
    evaluate,
    evaluate_ses_bounce_rate,
    evaluate_ses_complaint_rate,
)
from astrolift_operations.models import AlertRule
from astrolift_registry.models import RegisteredApp
from astrolift_services.email_observability import (
    register_driver_override,
    reset_driver_cache,
)
from astrolift_services.models import ManagedService

pytestmark = pytest.mark.django_db


# ---- driver stub ----------------------------------------------------


class _StatsOnlyDriver(EmailObservabilityDriver):
    """Returns scripted send-statistics; every other op raises Unsupported.

    The SES predicate handlers should only call get_send_statistics —
    if the dispatcher accidentally hits any other method this driver
    surfaces the deviation loudly rather than silently returning empty.
    """

    def __init__(self, points: list[SendStatPoint]) -> None:
        self._points = points
        self.call_count = 0

    def get_send_statistics(self) -> list[SendStatPoint]:
        self.call_count += 1
        return list(self._points)

    def get_send_quota(self) -> SendQuota:
        raise UnsupportedOperationError("unused in this test")

    def get_account_send_status(self) -> AccountSendStatus:
        raise UnsupportedOperationError("unused in this test")

    def get_identity_verification_details(self, identity: str) -> IdentityVerification:
        raise UnsupportedOperationError("unused in this test")

    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        raise UnsupportedOperationError("unused in this test")

    def list_suppression_entries(self, *, page_size: int = 100) -> list[SuppressionEntry]:
        raise UnsupportedOperationError("unused in this test")

    def add_suppression_entry(
        self, *, address: str, reason: SuppressionReason, note: str = ""
    ) -> SuppressionEntry:
        raise UnsupportedOperationError("unused in this test")

    def remove_suppression_entry(self, *, address: str) -> bool:
        raise UnsupportedOperationError("unused in this test")


# ---- scaffolding ----------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_driver_cache():
    reset_driver_cache()
    yield
    reset_driver_cache()


def _scaffold(*, plugin_slug: str = "aws", region: str = "us-east-1"):
    org = Organization.objects.create(
        name="Acme",
        slug=f"acme-ses-eval-{plugin_slug}",
    )
    team = Team.objects.create(
        organization=org,
        name="Eng",
        slug=f"eng-ses-eval-{plugin_slug}",
    )
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug=f"demo-ses-eval-{plugin_slug}",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=plugin_slug.upper(),
                slug=plugin_slug,
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug=plugin_slug)
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-ses-eval-{plugin_slug}",
        name=f"Cluster {plugin_slug}",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
        region=region,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {plugin_slug}",
        slug=f"app-ses-eval-{plugin_slug}",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "app"\n',
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return org, app, env


def _make_email_service(
    org: Any,
    app: Any,
    env: Any,
    *,
    region: str = "us-east-1",
) -> ManagedService:
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        variant="ses",
        name="ses",
        status=ManagedService.Status.ACTIVE,
        config={"region": region, "identity": "mail.example.com"},
    )


def _make_rule(
    org: Any,
    service: ManagedService | None,
    *,
    predicate: dict[str, Any] | None = None,
    name: str = "rule",
) -> AlertRule:
    return AlertRule.objects.create(
        organization=org,
        name=name,
        target=AlertRule.Target.APP,
        target_id=service.registered_app.slug if service else "",
        predicate=dict(predicate or {}),
        managed_service=service,
    )


def _points(
    *,
    attempts_per_bucket: int,
    bounces_per_bucket: int = 0,
    complaints_per_bucket: int = 0,
    bucket_count: int,
) -> list[SendStatPoint]:
    base = datetime(2026, 5, 1, tzinfo=UTC)
    return [
        SendStatPoint(
            timestamp=base + timedelta(minutes=15 * i),
            delivery_attempts=attempts_per_bucket,
            bounces=bounces_per_bucket,
            complaints=complaints_per_bucket,
            rejects=0,
        )
        for i in range(bucket_count)
    ]


# ---- registry sanity -----------------------------------------------


def test_kind_handlers_register_both_predicates():
    assert KIND_HANDLERS["ses_bounce_rate"] is evaluate_ses_bounce_rate
    assert KIND_HANDLERS["ses_complaint_rate"] is evaluate_ses_complaint_rate


def test_evaluate_returns_false_for_predicate_without_kind():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    rule = _make_rule(org, svc, predicate={"metric": "http_5xx_rate"})
    assert evaluate(rule) is False


def test_evaluate_returns_false_for_unknown_kind():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    rule = _make_rule(org, svc, predicate={"kind": "not_a_real_kind"})
    assert evaluate(rule) is False


def test_evaluate_swallows_handler_exception():
    """A handler that explodes must not propagate — the loop logs and
    moves on. The dispatcher's try/except is the safety net."""
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    rule = _make_rule(org, svc, predicate={"kind": "ses_bounce_rate"})

    class _Boom(EmailObservabilityDriver):
        def get_send_statistics(self) -> list[SendStatPoint]:
            raise RuntimeError("driver explosion")

        # Other methods unused — Protocol satisfaction needs them
        # present at type-check time, runtime missing is fine since
        # the predicate only touches get_send_statistics.
        def get_send_quota(self):
            raise NotImplementedError

        def get_account_send_status(self):
            raise NotImplementedError

        def get_identity_verification_details(self, identity):
            raise NotImplementedError

        def verify_dns_authentication(self, identity):
            raise NotImplementedError

        def list_suppression_entries(self, *, page_size=100):
            raise NotImplementedError

        def add_suppression_entry(self, *, address, reason, note=""):
            raise NotImplementedError

        def remove_suppression_entry(self, *, address):
            raise NotImplementedError

    register_driver_override(slug="aws", region="us-east-1", driver=_Boom())

    assert evaluate(rule) is False


# ---- bounce rate ----------------------------------------------------


def test_ses_bounce_rate_fires_above_threshold():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    # 100 buckets × 100 attempts = 10000 attempts; 100 buckets × 8 bounces
    # = 800 bounces ⇒ 8% bounce rate ⇒ fires above 5% threshold.
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(
            _points(
                attempts_per_bucket=100,
                bounces_per_bucket=8,
                bucket_count=100,
            )
        ),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={"kind": "ses_bounce_rate", "threshold_pct": 5.0},
    )
    assert evaluate_ses_bounce_rate(rule, rule.predicate) is True
    # Same answer through the dispatcher.
    assert evaluate(rule) is True


def test_ses_bounce_rate_quiet_below_threshold():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(
            _points(
                attempts_per_bucket=100,
                bounces_per_bucket=2,  # 2% bounce rate
                bucket_count=100,
            )
        ),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={"kind": "ses_bounce_rate", "threshold_pct": 5.0},
    )
    assert evaluate_ses_bounce_rate(rule, rule.predicate) is False


def test_ses_bounce_rate_no_attempts_no_fire():
    """Idle window: total_attempts == 0 must short-circuit to False
    rather than dividing by zero or treating "no signal" as bad."""
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(
            _points(
                attempts_per_bucket=0,
                bounces_per_bucket=0,
                bucket_count=672,
            )
        ),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={"kind": "ses_bounce_rate", "threshold_pct": 5.0},
    )
    assert evaluate_ses_bounce_rate(rule, rule.predicate) is False


def test_ses_bounce_rate_tail_slices_seven_days():
    """A driver returning more than 672 buckets gets sliced to the
    rightmost 672. Older buckets in the head should NOT participate.

    Construct: 700 buckets total. First 28 buckets carry 100 bounces /
    100 attempts (100% rate). Last 672 buckets carry 0 bounces / 100
    attempts (0% rate). 7d rate over the tail must read 0%, no fire."""
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    head = _points(attempts_per_bucket=100, bounces_per_bucket=100, bucket_count=28)
    tail = _points(attempts_per_bucket=100, bounces_per_bucket=0, bucket_count=672)
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(head + tail),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={"kind": "ses_bounce_rate", "threshold_pct": 1.0},
    )
    assert evaluate_ses_bounce_rate(rule, rule.predicate) is False


def test_ses_bounce_rate_default_threshold_is_five_percent():
    """Predicate without ``threshold_pct`` uses the 5.0% default."""
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(
            _points(
                attempts_per_bucket=100,
                bounces_per_bucket=6,  # 6% > 5% default
                bucket_count=10,
            )
        ),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={"kind": "ses_bounce_rate"},
    )
    assert evaluate_ses_bounce_rate(rule, rule.predicate) is True


def test_ses_bounce_rate_no_managed_service_no_fire():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    rule = _make_rule(
        org,
        None,
        predicate={"kind": "ses_bounce_rate", "threshold_pct": 0.0},
    )
    assert evaluate_ses_bounce_rate(rule, rule.predicate) is False


# ---- complaint rate ------------------------------------------------


def test_ses_complaint_rate_fires_above_threshold():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    # 100 attempts × 50 buckets = 5000 attempts; 2 complaints × 50 = 100
    # complaints ⇒ 2% complaint rate ⇒ fires above 0.5% threshold.
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(
            _points(
                attempts_per_bucket=100,
                complaints_per_bucket=2,
                bucket_count=50,
            )
        ),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={
            "kind": "ses_complaint_rate",
            "threshold_pct": 0.5,
        },
    )
    assert evaluate_ses_complaint_rate(rule, rule.predicate) is True


def test_ses_complaint_rate_quiet_below_threshold():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(
            _points(
                attempts_per_bucket=1000,
                complaints_per_bucket=0,  # 0% complaint rate
                bucket_count=50,
            )
        ),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={
            "kind": "ses_complaint_rate",
            "threshold_pct": 0.1,
        },
    )
    assert evaluate_ses_complaint_rate(rule, rule.predicate) is False


def test_ses_complaint_rate_default_threshold_is_point_one_percent():
    """No ``threshold_pct`` → 0.1% default; 0.2% complaint rate fires."""
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver(
            # 5000 attempts, 10 complaints ⇒ 0.2% rate
            _points(
                attempts_per_bucket=500,
                complaints_per_bucket=1,
                bucket_count=10,
            )
        ),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={"kind": "ses_complaint_rate"},
    )
    assert evaluate_ses_complaint_rate(rule, rule.predicate) is True


def test_ses_complaint_rate_no_attempts_no_fire():
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env)
    register_driver_override(
        slug="aws",
        region="us-east-1",
        driver=_StatsOnlyDriver([]),
    )
    rule = _make_rule(
        org,
        svc,
        predicate={
            "kind": "ses_complaint_rate",
            "threshold_pct": 0.0,
        },
    )
    assert evaluate_ses_complaint_rate(rule, rule.predicate) is False


# ---- driver resolution ---------------------------------------------


def test_ses_predicate_picks_region_from_service_config():
    """The driver must be resolved at the region the service config
    pins — not us-east-1 as a blanket fallback. We register a driver
    only at us-west-2 and confirm the eval reaches it."""
    org, app, env = _scaffold(plugin_slug="aws", region="us-east-1")
    svc = _make_email_service(org, app, env, region="us-west-2")
    driver = _StatsOnlyDriver(
        _points(
            attempts_per_bucket=100,
            bounces_per_bucket=10,
            bucket_count=50,
        )
    )
    register_driver_override(slug="aws", region="us-west-2", driver=driver)
    rule = _make_rule(
        org,
        svc,
        predicate={"kind": "ses_bounce_rate", "threshold_pct": 5.0},
    )
    assert evaluate_ses_bounce_rate(rule, rule.predicate) is True
    assert driver.call_count == 1
