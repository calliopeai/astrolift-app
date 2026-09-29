"""Operator allowlists for service accounts a GCP managed-service config names.

Tenants share the install's project. A config field that chooses the service
account a Google resource runs as, mints tokens for, or writes with (a
workflow's identity, an Eventarc or Pub/Sub push token, a gateway's backend
identity) is honoured with the platform's ``actAs``, so an unchecked field
lets a tenant run a platform resource as any account in the project (#1960,
#2087). Each such field must name an account the operator listed for that
driver in the cluster's ``provider_config``; an empty list refuses every one.

Google takes an account as its email, as a resource name ending in
``/serviceAccounts/<email>``, or as an IAM member ``serviceAccount:<email>``.
Only the email is compared, case-blind, since the email names the account
whichever project the resource name spells. Any other spelling (a unique id,
``projects/<p>/accounts/<id>``) matches no listed email and is refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


def unlisted_service_account(value: Any, allowed: Iterable[str]) -> str:
    """The account ``value`` names when ``allowed`` does not list it, else ``""``.

    An absent or empty ``value`` names no account, so it is never unlisted.
    """
    account = str(value or "").strip()
    if not account:
        return ""
    email = account.rsplit("/serviceAccounts/", 1)[-1].removeprefix("serviceAccount:")
    listed = {str(item).strip().casefold() for item in allowed}
    return "" if email.casefold() in listed else email
