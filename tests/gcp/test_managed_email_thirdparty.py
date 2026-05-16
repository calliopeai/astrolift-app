"""Tests for the GCP email stub driver (#375).

GCP doesn't ship a first-party transactional email service; the
stub driver exists so the (kind, variant) catalog is complete.
Every lifecycle entry raises NotImplementedError; read-only
schemas are populated so docs render.
"""

from __future__ import annotations

from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from gcp.managed.email_thirdparty import (
    KIND,
    GCPEmailStubConfig,
    GCPEmailStubDriver,
)


@pytest.fixture
def driver() -> GCPEmailStubDriver:
    return GCPEmailStubDriver(
        config=GCPEmailStubConfig(project_id="acme-prod"),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="gcp-prod",
        service_handle_hint="email",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


def test_kind_is_email() -> None:
    assert KIND == "email"


def test_driver_constructs_without_config() -> None:
    """The dataclass defaults must allow the no-arg path so a future
    real driver doesn't break the entry point."""
    d = GCPEmailStubDriver()
    assert d is not None


def test_provision_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    with pytest.raises(NotImplementedError) as exc_info:
        driver.provision(_spec())
    assert "GCP" in str(exc_info.value)
    # Operator-facing pointer to a supported path
    msg = str(exc_info.value).lower()
    assert "sendgrid" in msg or "mailgun" in msg


def test_update_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.update(UpdateSpec(handle="email/x"))


def test_deprovision_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.deprovision(DeprovisionSpec(handle="email/x"))


def test_deprovision_with_flags_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    """Stub must remain not-implemented across the four-corner matrix
    -- no axis silently swallows the call."""
    for delete_data, force_destroy in (
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ):
        with pytest.raises(NotImplementedError):
            driver.deprovision(
                DeprovisionSpec(handle="email/x"),
                delete_data=delete_data,
                force_destroy=force_destroy,
            )


def test_status_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.status(ServiceHandle(handle="email/x"))


def test_binding_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.binding(ServiceHandle(handle="email/x"))


def test_snapshot_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.snapshot(ServiceHandle(handle="email/x"))


def test_restore_raises_not_implemented(
    driver: GCPEmailStubDriver,
) -> None:
    snap = SnapshotHandle(
        handle="email/x", snapshot_id="snap-1", created_at="",
    )
    with pytest.raises(NotImplementedError):
        driver.restore(snap, _spec())


def test_config_schema_documents_stub_status(
    driver: GCPEmailStubDriver,
) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    assert "Stub" in schema["description"]


def test_binding_schema_lists_contract_envs(
    driver: GCPEmailStubDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "EMAIL_PROVIDER",
        "EMAIL_API_KEY",
        "EMAIL_FROM_ADDRESS",
        "EMAIL_REGION",
    ):
        assert key in schema.env_vars
