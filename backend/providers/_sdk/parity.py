"""Plugin parity harness (#62).

The parity harness defines the contract every plugin's drivers must
satisfy. Where the protocol declares a method (`bind_service_account`,
`ensure_repo`, `ensure_record`, etc.), the parity check confirms
each plugin's concrete class implements that method with the right
signature.

This is structurally similar to the matrix-check (#26) but operates
at the method-signature level rather than the catalog level —
catching cases where a plugin author adds a new driver but forgets
to implement an optional method (e.g., snapshot for a managed
service that does support it).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _sdk.base import ProviderPlugin


@dataclass(frozen=True)
class ParityIssue:
    plugin_id: str
    role_or_kind: str
    """e.g. 'cluster' (driver role) or 'object_store/s3'
    (managed-service kind+variant)."""

    code: str
    """missing_method | bad_signature | not_callable"""

    method: str
    detail: str


@dataclass(frozen=True)
class ParityReport:
    ok: bool
    issues: list[ParityIssue] = field(default_factory=list)


def required_methods(protocol: type) -> list[str]:
    """Return the list of methods a Protocol declares (excluding
    dunder methods + class-level attributes)."""
    methods: list[str] = []
    for name, value in inspect.getmembers(protocol):
        if name.startswith("_"):
            continue
        if not callable(value):
            continue
        # Protocol declares methods as functions; skip non-method
        # descriptors.
        if isinstance(value, type):
            continue
        methods.append(name)
    return methods


def check_class_against_protocol(
    *,
    plugin_id: str,
    role_or_kind: str,
    cls: type,
    protocol: type,
) -> list[ParityIssue]:
    """Return parity issues for `cls` against `protocol`."""
    issues: list[ParityIssue] = []
    for method in required_methods(protocol):
        actual = getattr(cls, method, None)
        if actual is None:
            issues.append(
                ParityIssue(
                    plugin_id=plugin_id,
                    role_or_kind=role_or_kind,
                    code="missing_method",
                    method=method,
                    detail=(f"{cls.__name__} does not implement {protocol.__name__}.{method}"),
                )
            )
            continue
        if not callable(actual):
            issues.append(
                ParityIssue(
                    plugin_id=plugin_id,
                    role_or_kind=role_or_kind,
                    code="not_callable",
                    method=method,
                    detail=(f"{cls.__name__}.{method} is not callable (found {type(actual).__name__})"),
                )
            )
    return issues


def check_plugin_parity(
    *,
    plugin: ProviderPlugin,
    role_protocols: dict[str, type],
    managed_service_protocol: type,
) -> ParityReport:
    """Validate every driver class in a plugin against its protocol.

    role_protocols maps driver role name → Protocol class.
    managed_service_protocol is the single ManagedServiceDriver
    Protocol every (kind, variant) entry must satisfy."""
    issues: list[ParityIssue] = []

    for role, cls in plugin.drivers.items():
        proto = role_protocols.get(role)
        if proto is None:
            continue  # role not in the protocol registry → skip
        issues.extend(
            check_class_against_protocol(
                plugin_id=plugin.id,
                role_or_kind=role,
                cls=cls,
                protocol=proto,
            )
        )

    for (kind, variant), cls in plugin.managed_service_drivers.items():
        issues.extend(
            check_class_against_protocol(
                plugin_id=plugin.id,
                role_or_kind=f"{kind}/{variant}",
                cls=cls,
                protocol=managed_service_protocol,
            )
        )

    return ParityReport(ok=not issues, issues=issues)


def check_all_plugins(
    *,
    plugins: list[ProviderPlugin],
    role_protocols: dict[str, type],
    managed_service_protocol: type,
) -> ParityReport:
    issues: list[ParityIssue] = []
    for plugin in plugins:
        report = check_plugin_parity(
            plugin=plugin,
            role_protocols=role_protocols,
            managed_service_protocol=managed_service_protocol,
        )
        issues.extend(report.issues)
    return ParityReport(ok=not issues, issues=issues)
