"""Azure ACS email observability stub tests."""

from __future__ import annotations

import pytest

from _sdk import UnsupportedOperationError
from _sdk.email import SuppressionReason
from azure.managed.email_obs import AzureAcsEmailObservabilityDriver


@pytest.fixture
def driver() -> AzureAcsEmailObservabilityDriver:
    return AzureAcsEmailObservabilityDriver()


def test_get_send_quota_raises_unsupported(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError) as exc:
        driver.get_send_quota()
    assert "ACS" in str(exc.value) or "Azure" in str(exc.value)


def test_get_send_statistics_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_send_statistics()


def test_account_send_status_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_account_send_status()


def test_identity_verification_details_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_identity_verification_details("example.com")


def test_verify_dns_authentication_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.verify_dns_authentication("example.com")


def test_list_suppression_entries_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.list_suppression_entries()


def test_add_suppression_entry_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.add_suppression_entry(
            address="bouncy@example.com",
            reason=SuppressionReason.MANUAL,
        )


def test_remove_suppression_entry_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.remove_suppression_entry(address="bouncy@example.com")


def test_list_templates_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.list_templates()


def test_get_template_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_template(name="welcome")


def test_create_template_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.create_template(
            name="welcome",
            subject="s",
            html_body="h",
            text_body="t",
        )


def test_update_template_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.update_template(
            name="welcome",
            subject="s",
            html_body="h",
            text_body="t",
        )


def test_delete_template_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.delete_template(name="welcome")


def test_get_template_send_statistics_raises(
    driver: AzureAcsEmailObservabilityDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.get_template_send_statistics(name="welcome")
