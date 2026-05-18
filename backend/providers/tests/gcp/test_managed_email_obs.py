"""GCP email observability stub tests.

Every method on :class:`GcpEmailObservabilityDriver` must raise
:class:`UnsupportedOperationError` with a message that points operators
at the third-party provider's console.
"""

from __future__ import annotations

import pytest

from _sdk import UnsupportedOperationError
from _sdk.email import SuppressionReason
from gcp.managed.email_obs import GcpEmailObservabilityDriver


@pytest.fixture
def driver() -> GcpEmailObservabilityDriver:
    return GcpEmailObservabilityDriver()


def test_get_send_quota_raises_unsupported(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError) as exc:
        driver.get_send_quota()
    assert "GCP" in str(exc.value)


def test_get_send_statistics_raises(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_send_statistics()


def test_account_send_status_raises(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_account_send_status()


def test_identity_verification_details_raises(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_identity_verification_details("example.com")


def test_verify_dns_authentication_raises(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.verify_dns_authentication("example.com")


def test_list_suppression_entries_raises(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.list_suppression_entries()


def test_add_suppression_entry_raises(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.add_suppression_entry(
            address="bouncy@example.com",
            reason=SuppressionReason.MANUAL,
        )


def test_remove_suppression_entry_raises(
    driver: GcpEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.remove_suppression_entry(address="bouncy@example.com")
