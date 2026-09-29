from __future__ import annotations

import pytest

from gcp._service_accounts import unlisted_service_account

_LISTED = ("runner@acme-prod.iam.gserviceaccount.com",)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "runner@acme-prod.iam.gserviceaccount.com",
        " Runner@ACME-prod.iam.gserviceaccount.com ",
        "projects/-/serviceAccounts/runner@acme-prod.iam.gserviceaccount.com",
        "projects/other/serviceAccounts/runner@acme-prod.iam.gserviceaccount.com",
        "serviceAccount:runner@acme-prod.iam.gserviceaccount.com",
    ],
)
def test_absent_or_listed_accounts_pass(value: object) -> None:
    assert unlisted_service_account(value, _LISTED) == ""


@pytest.mark.parametrize(
    ("value", "named"),
    [
        ("admin@acme-prod.iam.gserviceaccount.com", "admin@acme-prod.iam.gserviceaccount.com"),
        (
            "projects/-/serviceAccounts/admin@acme-prod.iam.gserviceaccount.com",
            "admin@acme-prod.iam.gserviceaccount.com",
        ),
        ("serviceAccount:admin@acme-prod.iam.gserviceaccount.com", "admin@acme-prod.iam.gserviceaccount.com"),
        ("109876543210987654321", "109876543210987654321"),
        ("projects/acme-prod/accounts/109876543210987654321", "projects/acme-prod/accounts/109876543210987654321"),
        ("user:runner@acme-prod.iam.gserviceaccount.com", "user:runner@acme-prod.iam.gserviceaccount.com"),
    ],
)
def test_unlisted_accounts_are_named(value: str, named: str) -> None:
    assert unlisted_service_account(value, _LISTED) == named


def test_an_empty_allowlist_refuses_every_account() -> None:
    assert unlisted_service_account("runner@acme-prod.iam.gserviceaccount.com", ()) != ""
