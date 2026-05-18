"""Backend GraphQL surface tests for email observability (#629, #631, #632, #633, #634).

Covers:
* astrolift_email_service_detail composite query (cloud-driver routing,
  partial-failure tolerance, unsupported-notes accumulation)
* addEmailSuppressionEntry + removeEmailSuppressionEntry mutations
  (permission gate, audit emission, MutationResult envelope shape,
  idempotent remove)
* tenant-scoped resolver guardrail (negative permission cases)

Driver round-trips are stubbed via the
``register_driver_override`` test seam so the suite stays hermetic —
moto coverage for the AWS driver lives in
``backend/providers/tests/aws/test_managed_email_ses_obs.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from _sdk import UnsupportedOperationError
from _sdk.email import (
    AccountSendStatus,
    DkimToken,
    DnsAuthCheck,
    DnsAuthStatus,
    DnsCheckOutcome,
    EmailObservabilityDriver,
    IdentityVerification,
    SendQuota,
    SendStatPoint,
    SuppressionEntry,
    SuppressionReason,
)
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.email_observability import (
    register_driver_override,
    reset_driver_cache,
)
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import (
    AddEmailSuppressionEntryInput,
    RemoveEmailSuppressionEntryInput,
    ServicesMutation,
)
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission, PermissionDenied  # noqa: F401 -- used in query permission test
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- Fakes -----------------------------------------------------------


class _RecordingDriver(EmailObservabilityDriver):
    """Records every call + returns scripted responses.

    Replaces the AWS / GCP / Azure drivers under the resolver path so
    the GraphQL surface tests don't go anywhere near boto3 or DNS.
    """

    def __init__(
        self,
        *,
        quota: SendQuota | None = None,
        send_statistics: list[SendStatPoint] | None = None,
        account_status: AccountSendStatus | None = None,
        identity_verification: IdentityVerification | None = None,
        dns_auth_status: DnsAuthStatus | None = None,
        suppression_entries: list[SuppressionEntry] | None = None,
    ) -> None:
        self.quota = quota
        self.send_statistics = send_statistics or []
        self.account_status = account_status
        self.identity_verification = identity_verification
        self.dns_auth_status = dns_auth_status
        self.suppression_entries = (
            list(suppression_entries) if suppression_entries else []
        )
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def get_send_quota(self) -> SendQuota:
        self._record("get_send_quota")
        if self.quota is None:
            raise RuntimeError("quota not configured")
        return self.quota

    def get_send_statistics(self) -> list[SendStatPoint]:
        self._record("get_send_statistics")
        return list(self.send_statistics)

    def get_account_send_status(self) -> AccountSendStatus:
        self._record("get_account_send_status")
        if self.account_status is None:
            raise RuntimeError("account_status not configured")
        return self.account_status

    def get_identity_verification_details(
        self, identity: str
    ) -> IdentityVerification:
        self._record("get_identity_verification_details", identity)
        if self.identity_verification is None:
            raise RuntimeError("identity_verification not configured")
        return self.identity_verification

    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        self._record("verify_dns_authentication", identity)
        if self.dns_auth_status is None:
            raise RuntimeError("dns_auth_status not configured")
        return self.dns_auth_status

    def list_suppression_entries(
        self, *, page_size: int = 100
    ) -> list[SuppressionEntry]:
        self._record("list_suppression_entries", page_size=page_size)
        return list(self.suppression_entries)

    def add_suppression_entry(
        self,
        *,
        address: str,
        reason: SuppressionReason,
        note: str = "",
    ) -> SuppressionEntry:
        self._record(
            "add_suppression_entry",
            address=address,
            reason=reason,
            note=note,
        )
        entry = SuppressionEntry(
            address=address,
            reason=reason,
            suppressed_at=datetime.now(UTC),
            detail=note,
        )
        self.suppression_entries.append(entry)
        return entry

    def remove_suppression_entry(self, *, address: str) -> bool:
        self._record("remove_suppression_entry", address=address)
        before = len(self.suppression_entries)
        self.suppression_entries = [
            e for e in self.suppression_entries if e.address != address
        ]
        return len(self.suppression_entries) < before


class _UnsupportedDriver(EmailObservabilityDriver):
    """Raises UnsupportedOperationError on every call.

    Mirrors what the GCP and Azure stubs do; lets the resolver tests
    confirm the partial-failure path collects unsupported-notes rather
    than letting the exception leak.
    """

    _MSG = "unsupported in tests"

    def get_send_quota(self) -> SendQuota:
        raise UnsupportedOperationError(self._MSG)

    def get_send_statistics(self) -> list[SendStatPoint]:
        raise UnsupportedOperationError(self._MSG)

    def get_account_send_status(self) -> AccountSendStatus:
        raise UnsupportedOperationError(self._MSG)

    def get_identity_verification_details(
        self, identity: str
    ) -> IdentityVerification:
        raise UnsupportedOperationError(self._MSG)

    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        raise UnsupportedOperationError(self._MSG)

    def list_suppression_entries(
        self, *, page_size: int = 100
    ) -> list[SuppressionEntry]:
        raise UnsupportedOperationError(self._MSG)

    def add_suppression_entry(
        self,
        *,
        address: str,
        reason: SuppressionReason,
        note: str = "",
    ) -> SuppressionEntry:
        raise UnsupportedOperationError(self._MSG)

    def remove_suppression_entry(self, *, address: str) -> bool:
        raise UnsupportedOperationError(self._MSG)


# ---- Scaffolding ----------------------------------------------------


def _info(user=None, ip: str | None = None):
    if user is None:
        request = SimpleNamespace(user=None, META={})
    else:
        meta: dict[str, str] = {}
        if ip:
            meta["HTTP_X_FORWARDED_FOR"] = ip
        request = SimpleNamespace(user=user, META=meta)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str = "email-obs-test"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            "email": f"{username}@example.com",
            "first_name": "Op",
            "last_name": "Erator",
        },
    )
    return user


def _scaffold(*, plugin_slug: str = "aws", region: str = "us-east-1"):
    org = Organization.objects.create(name="Acme", slug=f"acme-email-{plugin_slug}")
    team = Team.objects.create(
        organization=org, name="Eng", slug=f"eng-email-{plugin_slug}"
    )
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug=f"demo-email-{plugin_slug}",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=plugin_slug.upper(),
                slug=plugin_slug,
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug=plugin_slug)
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{plugin_slug}",
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
        slug=f"app-{plugin_slug}",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "app"\n',
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return org, app, env


def _make_email_service(app, env, *, identity: str = "ses.example.com"):
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        variant="ses",
        name="ses",
        status=ManagedService.Status.ACTIVE,
        config={"identity": identity, "email_from": f"noreply@{identity}"},
    )


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture(autouse=True)
def _reset_driver_cache():
    """Driver cache is process-local and bleeds across tests; reset
    around every test so each one starts fresh."""
    reset_driver_cache()
    yield
    reset_driver_cache()


# ---- astrolift_email_service_detail query ---------------------------


def test_email_detail_full_payload(permission_resolver):
    org, app, env = _scaffold()
    svc = _make_email_service(app, env, identity="full.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    fake = _RecordingDriver(
        quota=SendQuota(
            max_send_rate=14.0, max_24_hour_send=50000.0, sent_last_24h=210.0,
        ),
        account_status=AccountSendStatus(
            sending_enabled=True,
            production_access=True,
            reputation_score=0.93,
            bounce_rate_pct=0.4,
            complaint_rate_pct=0.02,
        ),
        identity_verification=IdentityVerification(
            identity="full.example.com",
            is_domain=True,
            status="Success",
            verification_token="abc-token",
            dkim_tokens=[
                DkimToken(
                    token="abc",
                    cname_host="abc._domainkey.full.example.com",
                    cname_target="abc.dkim.amazonses.com",
                ),
            ],
        ),
        dns_auth_status=DnsAuthStatus(
            identity="full.example.com",
            checked_at=datetime.now(UTC),
            dkim=DnsAuthCheck(
                protocol="DKIM", outcome=DnsCheckOutcome.GREEN,
            ),
            spf=DnsAuthCheck(
                protocol="SPF", outcome=DnsCheckOutcome.GREEN,
            ),
            dmarc=DnsAuthCheck(
                protocol="DMARC", outcome=DnsCheckOutcome.GREEN,
            ),
        ),
        suppression_entries=[
            SuppressionEntry(
                address="bouncy@example.com",
                reason=SuppressionReason.BOUNCE,
                suppressed_at=datetime.now(UTC),
            ),
        ],
    )
    register_driver_override(slug="aws", region="us-east-1", driver=fake)

    with _ctx(org):
        result = ServicesQuery().astrolift_email_service_detail(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result is not None
    assert result.plugin_slug == "aws"
    assert result.identity == "full.example.com"
    assert result.quota is not None
    assert result.quota.max_send_rate == 14.0
    assert result.account_status is not None
    assert result.account_status.production_access is True
    assert result.account_status.reputation_score == pytest.approx(0.93)
    assert result.identity_verification is not None
    assert result.identity_verification.status == "Success"
    assert len(result.identity_verification.dkim_tokens) == 1
    assert result.dns_auth_status is not None
    assert result.dns_auth_status.overall == "GREEN"
    assert len(result.suppression_entries) == 1
    assert result.suppression_entries[0].reason == "BOUNCE"
    assert result.unsupported_notes == []


def test_email_detail_unsupported_collects_notes(permission_resolver):
    org, app, env = _scaffold(plugin_slug="gcp", region="us-west1")
    svc = _make_email_service(app, env, identity="ses.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    register_driver_override(
        slug="gcp", region="us-west1", driver=_UnsupportedDriver(),
    )

    with _ctx(org):
        result = ServicesQuery().astrolift_email_service_detail(
            _info(_make_user("u-gcp")),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result is not None
    assert result.plugin_slug == "gcp"
    assert result.quota is None
    assert result.account_status is None
    assert result.identity_verification is None
    assert result.dns_auth_status is None
    assert result.suppression_entries == []
    # 5 unsupported notes (quota, account, identity, dns, suppression).
    assert len(result.unsupported_notes) == 5
    assert all("unsupported in tests" in note for note in result.unsupported_notes)


def test_email_detail_partial_failure_keeps_other_tiles(permission_resolver):
    """A driver where one method raises a generic Exception leaves the
    rest of the payload intact + accumulates the error in
    ``unsupported_notes``."""
    org, app, env = _scaffold()
    svc = _make_email_service(app, env, identity="partial.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    # Quota raises, everything else works.
    fake = _RecordingDriver(
        quota=None,  # raises in _RecordingDriver.get_send_quota
        account_status=AccountSendStatus(
            sending_enabled=True,
            production_access=False,
            reputation_score=None,
            bounce_rate_pct=None,
            complaint_rate_pct=None,
        ),
        identity_verification=IdentityVerification(
            identity="partial.example.com",
            is_domain=True,
            status="Pending",
            verification_token="",
            dkim_tokens=[],
        ),
        dns_auth_status=DnsAuthStatus(
            identity="partial.example.com",
            checked_at=datetime.now(UTC),
            dkim=DnsAuthCheck(
                protocol="DKIM", outcome=DnsCheckOutcome.RED,
            ),
            spf=DnsAuthCheck(
                protocol="SPF", outcome=DnsCheckOutcome.RED,
            ),
            dmarc=DnsAuthCheck(
                protocol="DMARC", outcome=DnsCheckOutcome.RED,
            ),
        ),
    )
    register_driver_override(slug="aws", region="us-east-1", driver=fake)

    with _ctx(org):
        result = ServicesQuery().astrolift_email_service_detail(
            _info(_make_user("u-partial")),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result is not None
    # Quota tile is null but the rest loaded fine.
    assert result.quota is None
    assert result.account_status is not None
    assert result.identity_verification is not None
    assert result.dns_auth_status is not None
    # Note carried for the broken tile, none for the working ones.
    assert any("quota" in note for note in result.unsupported_notes)
    assert not any("account_status" in note for note in result.unsupported_notes)


def test_email_detail_returns_none_for_missing_svc(permission_resolver):
    org, _app, _env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    with _ctx(org):
        result = ServicesQuery().astrolift_email_service_detail(
            _info(_make_user("u-missing")),
            managed_service_id=GUID("00000000-0000-0000-0000-000000000000"),
        )
    assert result is None


def test_email_detail_returns_none_for_non_email_kind(permission_resolver):
    org, app, env = _scaffold()
    # postgres kind, not email — resolver short-circuits.
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        variant="rds",
        name="db",
        status=ManagedService.Status.ACTIVE,
    )
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    with _ctx(org):
        result = ServicesQuery().astrolift_email_service_detail(
            _info(_make_user("u-pg")),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result is None


def test_email_detail_requires_permission():
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    # Don't grant any permission — resolver-entry decorator denies.
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            ServicesQuery().astrolift_email_service_detail(
                _info(_make_user("u-deny")),
                managed_service_id=GUID(str(svc.guid)),
            )


# ---- addEmailSuppressionEntry --------------------------------------


def test_add_suppression_round_trip(permission_resolver):
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    fake = _RecordingDriver()
    register_driver_override(slug="aws", region="us-east-1", driver=fake)

    with _ctx(org):
        result = ServicesMutation().add_email_suppression_entry(
            _info(_make_user("u-add"), ip="1.2.3.4"),
            input=AddEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="bouncy@example.com",
                reason="MANUAL",
                note="ticket #4567",
            ),
        )
    assert result.ok is True
    assert result.data is not None
    assert result.data.address == "bouncy@example.com"
    assert result.data.reason == "MANUAL"

    # Driver got the right call.
    add_calls = [c for c in fake.calls if c[0] == "add_suppression_entry"]
    assert len(add_calls) == 1
    assert add_calls[0][2]["address"] == "bouncy@example.com"
    assert add_calls[0][2]["reason"] == SuppressionReason.MANUAL
    assert add_calls[0][2]["note"] == "ticket #4567"


def test_add_suppression_validates_address(permission_resolver):
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    register_driver_override(
        slug="aws", region="us-east-1", driver=_RecordingDriver(),
    )

    with _ctx(org):
        result = ServicesMutation().add_email_suppression_entry(
            _info(_make_user("u-add-blank")),
            input=AddEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="   ",
                reason="MANUAL",
            ),
        )
    assert result.ok is False
    assert result.errors[0].field == "address"


def test_add_suppression_rejects_bad_reason(permission_resolver):
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    register_driver_override(
        slug="aws", region="us-east-1", driver=_RecordingDriver(),
    )

    with _ctx(org):
        result = ServicesMutation().add_email_suppression_entry(
            _info(_make_user("u-add-bad")),
            input=AddEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="x@example.com",
                reason="GARBAGE",
            ),
        )
    assert result.ok is False
    assert result.errors[0].field == "reason"


def test_add_suppression_requires_permission():
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    # No grants — @mutation_audit translates PermissionDenied to a
    # MutationResult envelope with code=PERMISSION_DENIED.
    with _ctx(org):
        result = ServicesMutation().add_email_suppression_entry(
            _info(_make_user("u-add-deny")),
            input=AddEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="bouncy@example.com",
                reason="MANUAL",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_add_suppression_unsupported_cloud_returns_precondition(
    permission_resolver,
):
    org, app, env = _scaffold(plugin_slug="azure", region="westus")
    svc = _make_email_service(app, env, identity="acs.example.com")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    register_driver_override(
        slug="azure", region="westus", driver=_UnsupportedDriver(),
    )
    with _ctx(org):
        result = ServicesMutation().add_email_suppression_entry(
            _info(_make_user("u-az-add")),
            input=AddEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="bouncy@example.com",
                reason="MANUAL",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"


# ---- removeEmailSuppressionEntry -----------------------------------


def test_remove_suppression_idempotent(permission_resolver):
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    # Pre-seed one entry on the recording driver so the first remove
    # has something to take off.
    fake = _RecordingDriver(
        suppression_entries=[
            SuppressionEntry(
                address="bouncy@example.com",
                reason=SuppressionReason.BOUNCE,
                suppressed_at=datetime.now(UTC),
            ),
        ],
    )
    register_driver_override(slug="aws", region="us-east-1", driver=fake)

    with _ctx(org):
        r1 = ServicesMutation().remove_email_suppression_entry(
            _info(_make_user("u-rm-1")),
            input=RemoveEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="bouncy@example.com",
            ),
        )
        r2 = ServicesMutation().remove_email_suppression_entry(
            _info(_make_user("u-rm-2")),
            input=RemoveEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="bouncy@example.com",
            ),
        )
    assert r1.ok is True
    assert r1.data is not None
    assert r1.data.removed is True
    assert r2.ok is True
    assert r2.data is not None
    assert r2.data.removed is False  # idempotent: address gone already


def test_remove_suppression_requires_permission():
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    with _ctx(org):
        result = ServicesMutation().remove_email_suppression_entry(
            _info(_make_user("u-rm-deny")),
            input=RemoveEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="bouncy@example.com",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_remove_suppression_validates_address(permission_resolver):
    org, app, env = _scaffold()
    svc = _make_email_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    register_driver_override(
        slug="aws", region="us-east-1", driver=_RecordingDriver(),
    )
    with _ctx(org):
        result = ServicesMutation().remove_email_suppression_entry(
            _info(_make_user("u-rm-blank")),
            input=RemoveEmailSuppressionEntryInput(
                managed_service_id=GUID(str(svc.guid)),
                address="   ",
            ),
        )
    assert result.ok is False
    assert result.errors[0].field == "address"


def test_remove_suppression_not_found_service(permission_resolver):
    org, _app, _env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    with _ctx(org):
        result = ServicesMutation().remove_email_suppression_entry(
            _info(_make_user("u-rm-missing")),
            input=RemoveEmailSuppressionEntryInput(
                managed_service_id=GUID(
                    "00000000-0000-0000-0000-000000000000"
                ),
                address="bouncy@example.com",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
