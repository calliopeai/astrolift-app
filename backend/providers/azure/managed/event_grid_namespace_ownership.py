"""Saved placement and local receipts for Event Grid's untaggable children.

These receipts require independently verified namespace ownership and complete
cloud inventories at the call site. Writable Key Vault state is not proof of
an immutable cloud incarnation. A reservation never grants resource authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from _sdk.azure_ownership import AzureOperation, AzureOwnershipError, owner_of, verify_azure_ownership


class OwnershipUnknown(ValueError):
    """Current target or resource authority cannot be established."""


def canonical_uuid(value: str) -> UUID:
    try:
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError) as exc:
        raise OwnershipUnknown("a canonical nonzero immutable UUID is required") from exc


def service_uuid(source: object) -> UUID:
    return canonical_uuid(getattr(source, "managed_service_id", ""))


def field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def same_arm(actual: Any, expected: str) -> bool:
    return isinstance(actual, str) and actual.startswith("/") and actual.casefold() == expected.casefold()


def resource_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{1,48}[a-zA-Z0-9]", value):
        raise OwnershipUnknown("namespace and topic names require 3-50 safe characters")
    return value


@dataclass(frozen=True)
class Target:
    subscription: str
    resource_group: str
    namespace: str
    topic: str

    def __post_init__(self) -> None:
        canonical_uuid(self.subscription)
        if (
            not isinstance(self.resource_group, str)
            or not re.fullmatch(r"[\w.()\-]{1,90}", self.resource_group, flags=re.ASCII)
            or self.resource_group.endswith(".")
        ):
            raise OwnershipUnknown("resource group cannot be represented safely")
        resource_name(self.namespace)
        resource_name(self.topic)
        if len(self.handle) > 512 or len(self.topic_id) > 512:
            raise OwnershipUnknown("saved target exceeds the storage limit")

    @property
    def handle(self) -> str:
        return f"event_bus/arm-v1/{self.subscription}/{self.resource_group}/{self.namespace}/{self.topic}"

    @property
    def namespace_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.EventGrid/namespaces/{self.namespace}"
        )

    @property
    def topic_id(self) -> str:
        return f"{self.namespace_id}/topics/{self.topic}"

    def child_id(self, name: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9]{3,50}", name):
            raise OwnershipUnknown("Standard child names require 3-50 alphanumeric characters")
        return f"{self.topic_id}/eventSubscriptions/{name}"

    @classmethod
    def saved(cls, handle: str, *, subscription: str, resource_group: str, source: object) -> Target:
        service_uuid(source)
        parts = handle.split("/")
        if len(parts) != 6 or parts[:2] != ["event_bus", "arm-v1"]:
            raise OwnershipUnknown("historical Standard placement is not recorded unambiguously")
        target = cls(parts[2], parts[3], parts[4], parts[5])
        if target.handle != handle or (target.subscription, target.resource_group) != (subscription, resource_group):
            raise OwnershipUnknown("saved placement differs from the current provider")
        return target

    @classmethod
    def new(
        cls,
        *,
        subscription: str,
        resource_group: str,
        namespace_prefix: str,
        topic_prefix: str,
        source: object,
        config: dict[str, Any],
    ) -> Target:
        identity = service_uuid(source).hex

        def name(key: str, prefix: str) -> str:
            if key in config:
                value = resource_name(config[key])
                if key == "namespace_name" and value.casefold().startswith(("microsoft", "system", "eventgrid")):
                    raise OwnershipUnknown("namespace name uses a provider-reserved prefix")
                if identity not in value.casefold():
                    raise OwnershipUnknown("new explicit names must retain the full service UUID hex")
                return value
            if not isinstance(prefix, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,16}", prefix):
                raise OwnershipUnknown("new name prefixes require 1-17 safe characters")
            if key == "namespace_name" and prefix.casefold().startswith(("microsoft", "system", "eventgrid")):
                raise OwnershipUnknown("namespace prefix uses a provider-reserved prefix")
            return resource_name(f"{prefix}-{identity}")

        return cls(
            subscription, resource_group, name("namespace_name", namespace_prefix), name("topic_name", topic_prefix)
        )


def child_name(source: object, logical_name: str) -> str:
    if not isinstance(logical_name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{1,48}[a-zA-Z0-9]", logical_name):
        raise OwnershipUnknown("logical subscription names require 3-50 safe characters")
    digest = hashlib.sha256(logical_name.casefold().encode()).hexdigest()[:9]
    return f"astrolift{service_uuid(source).hex}{digest}"


def assert_identity(actual: Any, resource_id: str) -> None:
    if (
        not same_arm(field(actual, "id"), resource_id)
        or str(field(actual, "name") or "").casefold() != resource_id.rsplit("/", 1)[1].casefold()
    ):
        raise OwnershipUnknown("actual ARM identity does not match the exact saved resource")


def assert_namespace_owner(actual: Any, target: Target, source: object) -> None:
    assert_identity(actual, target.namespace_id)
    wanted = str(service_uuid(source))
    tags = field(actual, "tags")
    if not isinstance(tags, dict):
        raise OwnershipUnknown("current namespace ownership tags are unavailable")
    observed = False
    for key, value in tags.items():
        if not isinstance(key, str):
            raise OwnershipUnknown("namespace ownership tags are malformed")
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized in {"astroliftmanagedserviceid", "astroliftiomanagedserviceid", "xastroliftmanagedserviceid"}:
            observed = True
            if value != wanted:
                raise OwnershipUnknown("namespace source identity aliases disagree")
        if normalized in {"astroliftmanagedby", "astroliftiomanagedby", "xastroliftmanagedby"} and value != "platform":
            raise OwnershipUnknown("namespace platform identity aliases disagree")
    if not observed:
        raise OwnershipUnknown("current namespace source identity is missing")
    try:
        verify_azure_ownership(
            tags, owner_of(source), operation=AzureOperation.UPDATE, resource="saved Event Grid Standard namespace"
        )
    except AzureOwnershipError as exc:
        raise OwnershipUnknown("current namespace platform/source authority does not match") from exc


def contains(actual: dict[str, Any], desired: dict[str, Any]) -> bool:
    for key, value in desired.items():
        if key not in actual:
            return False
        if isinstance(value, dict):
            if not isinstance(actual[key], dict) or not contains(actual[key], value):
                return False
        elif actual[key] != value:
            return False
    return True


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnershipUnknown("registry JSON contains duplicate keys")
        result[key] = value
    return result


@dataclass
class Receipts:
    target: Target
    owner: str
    resources: dict[str, dict[str, Any]]

    @classmethod
    def empty(cls, target: Target, source: object) -> Receipts:
        return cls(target, str(service_uuid(source)), {})

    @property
    def secret_name(self) -> str:
        digest = hashlib.sha256(self.target.topic_id.casefold().encode()).hexdigest()[:16]
        return f"eventgridns-{canonical_uuid(self.owner).hex}-{digest}"

    def _resource_id(self, identity: str) -> str:
        if same_arm(identity, self.target.topic_id):
            return self.target.topic_id
        prefix = self.target.topic_id + "/eventSubscriptions/"
        if isinstance(identity, str) and identity.casefold().startswith(prefix.casefold()):
            name = identity[len(prefix) :]
            if re.fullmatch("astrolift" + canonical_uuid(self.owner).hex + "[a-f0-9]{9}", name.casefold()):
                return self.target.child_id(name)
        raise OwnershipUnknown("registry resource is outside the exact saved topic/source")

    @classmethod
    def load(cls, raw: str, target: Target, source: object) -> Receipts:
        if not isinstance(raw, str) or len(raw.encode()) > 2 * 1024 * 1024:
            raise OwnershipUnknown("registry exceeds the observation limit")
        try:
            value = json.loads(
                raw,
                object_pairs_hook=_unique_object,
                parse_constant=lambda _: (_ for _ in ()).throw(OwnershipUnknown("registry has a nonfinite value")),
            )
        except (ValueError, TypeError, RecursionError) as exc:
            raise OwnershipUnknown("registry JSON is unavailable or ambiguous") from exc
        owner = str(service_uuid(source))
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "owner", "topic_id", "resources"}
            or type(value["version"]) is not int
            or value["version"] != 2
            or value["owner"] != owner
            or not same_arm(value["topic_id"], target.topic_id)
            or not isinstance(value["resources"], dict)
            or len(value["resources"]) > 129
        ):
            raise OwnershipUnknown("legacy, foreign or malformed registry cannot grant resource authority")
        receipts = cls(target, owner, {})
        for key, record in value["resources"].items():
            identity = receipts._resource_id(key)
            if (
                key != identity.casefold()
                or not isinstance(record, dict)
                or set(record) != {"id", "state", "parameters"}
                or not same_arm(record["id"], identity)
                or not isinstance(record["state"], str)
                or record["state"] not in {"reserved", "accepted", "observed"}
                or not isinstance(record["parameters"], dict)
                or not record["parameters"]
            ):
                raise OwnershipUnknown("registry receipt is malformed or conflicts with its saved target")
            receipts.resources[key] = record
        return receipts

    def dump(self) -> str:
        raw = json.dumps(
            {"version": 2, "owner": self.owner, "topic_id": self.target.topic_id, "resources": self.resources},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        self.load(raw, self.target, type("Source", (), {"managed_service_id": self.owner})())
        return raw

    def reserve(self, identity: str, parameters: dict[str, Any]) -> None:
        key = self._resource_id(identity).casefold()
        if not isinstance(parameters, dict) or not parameters:
            raise OwnershipUnknown("reservation requires complete desired SDK parameters")
        if key in self.resources:
            raise OwnershipUnknown("existing receipt cannot be silently replaced by a reservation")
        self.resources[key] = {"id": identity, "state": "reserved", "parameters": deepcopy(parameters)}

    def accept(self, identity: str, actual: Any, parameters: dict[str, Any]) -> None:
        key = self._resource_id(identity).casefold()
        if not isinstance(parameters, dict) or not parameters:
            raise OwnershipUnknown("acceptance requires complete desired SDK parameters")
        if key not in self.resources:
            raise OwnershipUnknown("cloud acceptance requires a previously persisted receipt")
        assert_identity(actual, identity)
        self.resources[key] = {"id": identity, "state": "accepted", "parameters": deepcopy(parameters)}

    def assert_authority(self, identity: str, actual: Any) -> dict[str, Any]:
        key = self._resource_id(identity).casefold()
        record = self.resources.get(key)
        if record is None or record["state"] not in {"accepted", "observed"}:
            raise OwnershipUnknown("reserved-only or missing receipt does not grant resource authority")
        assert_identity(actual, identity)
        return record

    def observe(self, identity: str, actual: Any) -> bool:
        record = self.assert_authority(identity, actual)
        serializer = getattr(actual, "serialize", None)
        if not callable(serializer):
            raise OwnershipUnknown("actual typed SDK fields are unavailable")
        state = field(actual, "provisioning_state")
        if str(getattr(state, "value", state) or "") != "Succeeded" or not contains(serializer(), record["parameters"]):
            return False
        record["state"] = "observed"
        return True
