"""Operator allowlists for user-assigned identities an Azure config names.

Tenants share the install's subscription, and the platform may assign any
user-assigned managed identity there. A config field that attaches one to a
resource (an Event Grid topic delivering with it, Event Hubs Capture writing
with it, a customer-managed key unwrapped with it) lets that resource act as
the identity, so an unchecked field lets a tenant run a platform resource as
another tenant's or the platform's identity (#1960, #2087). Each such field
must name an identity the operator listed for that driver in the cluster's
``provider_config``; an empty list refuses every one, as
``faas_allowed_identity_resource_ids`` does.

ARM resource ids compare case-blind, and a trailing ``/`` is not part of one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


def unlisted_identity(value: Any, allowed: Iterable[str]) -> str:
    """The identity ``value`` names when ``allowed`` does not list it, else ``""``.

    An absent or empty ``value`` names no identity, so it is never unlisted.
    """
    identity = str(value or "").strip()
    if not identity:
        return ""
    listed = {str(item).strip().rstrip("/").casefold() for item in allowed}
    return "" if identity.rstrip("/").casefold() in listed else identity
