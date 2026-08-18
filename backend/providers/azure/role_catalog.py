"""The Azure grant contract: portable ``Grant.actions`` -> Azure RBAC (#1367).

Managed-service drivers declare portable grants. On AWS those become policy
statements; on Azure the only thing that actually authorizes a workload is a
``Microsoft.Authorization/roleAssignments`` write naming a *role definition ID*
at a *resource scope*. Display names drift per tenant and raw management action
strings are not assignable at all, so this module is the single place that
turns a declared action into one of exactly three outcomes:

``RoleGrant``
    The action maps to a stable built-in role definition GUID. The workload
    identity gets a role assignment at the grant's scope.

``ControlPlaneGrant``
    The action is one the Astrolift control plane performs with its own
    credentials during provision/bind — reading an ARM resource, listing keys,
    fetching a Key Vault secret. The resulting material is written to the
    ``astrolift-bindings-<app>`` Kubernetes Secret and reaches the pod through
    ``envFrom: secretRef``; the pod never calls ARM for it, so a role
    assignment on the workload identity would be pure over-permission. Each
    entry carries the reason so the classification stays auditable.

Anything else raises. A grant we cannot classify must never be silently
dropped: that is precisely the failure this contract exists to end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Built-in Azure RBAC role definition GUIDs. Referenced by ID because IDs are
# stable across tenants and clouds while display names are localizable.
AZURE_BUILTIN_ROLE_IDS: dict[str, str] = {
    "Azure Event Hubs Data Owner": "f526a384-b230-433a-b45c-95f59c4a2dec",
    "Azure Event Hubs Data Receiver": "a638d3c7-ab3a-418d-83e6-5f17a39d4fde",
    "Azure Event Hubs Data Sender": "2b629674-e913-4c01-ae53-ef4638d8f975",
    "Azure Service Bus Data Owner": "090c5cfd-751d-490a-894a-3ce6f1109419",
    "Azure Service Bus Data Receiver": "4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0",
    "Azure Service Bus Data Sender": "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39",
    "Cognitive Services OpenAI User": "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd",
    "Cognitive Services User": "a97b65f3-24c7-4388-baec-2e87135dc908",
    "EventGrid Contributor": "1e241071-0855-49ea-94dc-649edcd759de",
    "EventGrid Data Sender": "d5a91429-5739-47e2-a06b-3470a27159e7",
    "EventGrid EventSubscription Contributor": "428e0ff0-5e57-4d9c-a221-2c70d0e0a443",
    "Key Vault Crypto Officer": "14b46e9e-c2b7-41b4-b07b-48a6ebf60603",
    "Key Vault Crypto Service Encryption User": "e147488a-f6f5-4113-8e2d-b22465e65bf6",
    "Key Vault Crypto User": "12338af0-0e69-4776-bea7-57ae8d297424",
    "Key Vault Reader": "21090545-7ca7-4776-b22c-e363652d74d2",
    "Key Vault Secrets User": "4633458b-17de-408a-b874-0445c86b69e6",
    "Monitoring Data Reader": "b0d8363b-8ddd-447d-831f-62ca05bff136",
    "Monitoring Metrics Publisher": "3913510d-42f4-4e42-8a64-420c390055eb",
    "Reader": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "Search Index Data Contributor": "8ebe5a00-799e-43f5-93ac-243d3dce84a7",
    "Search Index Data Reader": "1407120a-92aa-4202-b7e9-c0e197c71c8f",
    "Storage Blob Data Contributor": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    "Storage Blob Data Owner": "b7e6dc6d-f1e8-4753-8033-0f276bb0955b",
    "Storage Blob Data Reader": "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
    "Storage File Data SMB Share Contributor": "0c867c2a-1d8c-454a-a3db-ab2ea1bdc8bb",
    "Storage File Data SMB Share Reader": "aba4ae5f-2193-4029-9191-0cb91df5e314",
}

_ROLE_ID_BY_GUID: dict[str, str] = {guid: name for name, guid in AZURE_BUILTIN_ROLE_IDS.items()}

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_ROLE_DEFINITION_PATH_RE = re.compile(
    r"^/(?:subscriptions/[^/]+/)?providers/Microsoft\.Authorization/roleDefinitions/([^/]+)$",
    re.IGNORECASE,
)

# An ARM scope is a subscription, a resource group, or a resource beneath one.
# Sub-resource paths (a blob container, a Service Bus queue) are legal scopes
# and are exactly what least-privilege binding needs, so the tail is open.
_ARM_SCOPE_RE = re.compile(
    r"^/subscriptions/[^/]+"
    r"(?:/resourceGroups/[^/]+"
    r"(?:/providers/[^/]+(?:/[^/]+/[^/]+)+)?)?$",
    re.IGNORECASE,
)


class AzureGrantContractError(ValueError):
    """A declared grant cannot be expressed as an Azure role assignment."""


@dataclass(frozen=True)
class RoleGrant:
    """An assignable built-in role."""

    role_name: str
    role_definition_guid: str


@dataclass(frozen=True)
class ControlPlaneGrant:
    """A grant the control plane satisfies; no workload role assignment."""

    reason: str


_SECRET_MATERIALIZED = (
    "connection material is fetched by the control plane and delivered to the "
    "pod as a projected Kubernetes Secret; the workload never calls ARM"
)
_PROVISION_TIME_READ = "ARM read performed by the control plane during provision/bind"

# The full classification of every action the Azure managed-service drivers
# declare. Adding a driver grant means adding a row here — that is the review
# gate, and an unlisted action fails the deploy rather than granting nothing.
AZURE_GRANT_ACTIONS: dict[str, RoleGrant | ControlPlaneGrant] = {
    # -- genuine data-plane access the pod performs itself ------------------
    "Microsoft.CognitiveServices/accounts/OpenAI/deployments/action": RoleGrant(
        "Cognitive Services OpenAI User",
        AZURE_BUILTIN_ROLE_IDS["Cognitive Services OpenAI User"],
    ),
    "Microsoft.Insights/dataCollectionRules/data/action": RoleGrant(
        "Monitoring Metrics Publisher",
        AZURE_BUILTIN_ROLE_IDS["Monitoring Metrics Publisher"],
    ),
    "Microsoft.Monitor/accounts/data/metrics/write": RoleGrant(
        "Monitoring Metrics Publisher",
        AZURE_BUILTIN_ROLE_IDS["Monitoring Metrics Publisher"],
    ),
    "Microsoft.Monitor/accounts/data/metrics/read": RoleGrant(
        "Monitoring Data Reader",
        AZURE_BUILTIN_ROLE_IDS["Monitoring Data Reader"],
    ),
    # -- control-plane operations ------------------------------------------
    "Microsoft.KeyVault/vaults/secrets/getSecret": ControlPlaneGrant(_SECRET_MATERIALIZED),
    "Microsoft.Cache/Redis/listKeys/action": ControlPlaneGrant(_SECRET_MATERIALIZED),
    "Microsoft.Communication/CommunicationServices/listKeys/action": ControlPlaneGrant(
        _SECRET_MATERIALIZED,
    ),
    "Microsoft.DocumentDB/databaseAccounts/listConnectionStrings/action": ControlPlaneGrant(
        _SECRET_MATERIALIZED,
    ),
    "Microsoft.Search/searchServices/listAdminKeys/action": ControlPlaneGrant(_SECRET_MATERIALIZED),
    "Microsoft.Cache/Redis/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Cache/redisEnterprise/databases/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.CognitiveServices/accounts/deployments/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Communication/CommunicationServices/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Communication/emailServices/domains/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.DBforMySQL/flexibleServers/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.DBforPostgreSQL/flexibleServers/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.DocumentDB/databaseAccounts/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Insights/dataCollectionRules/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Monitor/accounts/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Search/searchServices/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Sql/managedInstances/read": ControlPlaneGrant(_PROVISION_TIME_READ),
    "Microsoft.Sql/servers/databases/read": ControlPlaneGrant(_PROVISION_TIME_READ),
}


def resolve_grant_action(action: str) -> RoleGrant | ControlPlaneGrant:
    """Classify one declared grant action.

    Accepts a built-in role display name, a bare role definition GUID, a full
    ``.../providers/Microsoft.Authorization/roleDefinitions/<guid>`` path, or a
    management action listed in :data:`AZURE_GRANT_ACTIONS`.
    """
    candidate = (action or "").strip()
    if not candidate:
        raise AzureGrantContractError("Azure managed-service grant declared an empty action")

    if candidate in AZURE_BUILTIN_ROLE_IDS:
        return RoleGrant(candidate, AZURE_BUILTIN_ROLE_IDS[candidate])

    path_match = _ROLE_DEFINITION_PATH_RE.match(candidate)
    guid = path_match.group(1) if path_match else candidate
    if _GUID_RE.match(guid):
        known = _ROLE_ID_BY_GUID.get(guid.lower())
        if known is None:
            raise AzureGrantContractError(
                f"Azure grant names role definition {guid!r}, which is not in the "
                "vetted built-in catalog; add it to AZURE_BUILTIN_ROLE_IDS with the "
                "role it corresponds to",
            )
        return RoleGrant(known, guid.lower())

    classified = AZURE_GRANT_ACTIONS.get(candidate)
    if classified is None:
        raise AzureGrantContractError(
            f"Azure managed-service grants must name a built-in role or a classified "
            f"management action; received {candidate!r}. Raw management actions are not "
            "assignable, so accepting this would report a configured binding while "
            "granting the workload nothing.",
        )
    return classified


def validate_arm_scope(scope: str) -> str:
    """Return ``scope`` if it is a well-formed ARM scope, else raise."""
    candidate = (scope or "").strip().rstrip("/")
    if not _ARM_SCOPE_RE.match(candidate):
        raise AzureGrantContractError(
            f"Azure role assignments require an ARM resource scope; received {scope!r}. "
            "A grant naming a bare secret or an opaque handle cannot be scoped and must "
            "be classified as a control-plane grant instead.",
        )
    return candidate


def role_definition_resource_id(*, subscription_id: str, role_definition_guid: str) -> str:
    """Build the subscription-qualified role definition ID ARM expects."""
    return f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{role_definition_guid}"


def subscription_of(scope: str) -> str:
    """Extract the subscription segment from an ARM scope."""
    return validate_arm_scope(scope).split("/")[2]
