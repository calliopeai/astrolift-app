"""Hybrid plugin composition (#54).

Lets one plugin delegate specific drivers/managed-services to
another plugin's implementations. The motivating use case:
operator wants to deploy on AWS EKS but use CNPG (in-cluster
Postgres) instead of RDS — cheaper, more portable, but needs
the AWS plugin to know to route ('postgres', 'cnpg') to the
k8s_native plugin's CNPGPostgresDriver.

Composition is opt-in per (kind, variant) pair. The platform's
cluster validator + binding resolver consult the registry to
find the implementing plugin.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any


@dataclasses.dataclass(frozen=True)
class DelegatedDriver:
    """A driver provided by one plugin but registered as
    available under another."""

    target_plugin_id: str
    """Which plugin's manifest registers the driver. The
    implementing class lives in the source plugin."""

    role: str
    """Driver role name: 'cluster', 'ingress', 'dns', 'tls',
    'secrets', 'identity', 'registry'."""

    source_plugin_id: str
    """Where the implementation lives."""


@dataclasses.dataclass(frozen=True)
class DelegatedManagedService:
    """A managed-service driver routed across plugin boundaries."""

    target_plugin_id: str
    kind: str
    variant: str
    source_plugin_id: str


@dataclasses.dataclass
class CompositionRegistry:
    """Captured the operator-defined composition rules. Built at
    install time, consumed by the resolver at every workflow
    step that picks a driver."""

    delegated_drivers: list[DelegatedDriver] = dataclasses.field(
        default_factory=list,
    )
    delegated_managed_services: list[DelegatedManagedService] = dataclasses.field(
        default_factory=list,
    )

    def delegate_driver(
        self,
        *,
        target_plugin_id: str,
        role: str,
        source_plugin_id: str,
    ) -> None:
        if target_plugin_id == source_plugin_id:
            raise ValueError(
                "delegation requires distinct target + source plugins",
            )
        self.delegated_drivers.append(DelegatedDriver(
            target_plugin_id=target_plugin_id,
            role=role,
            source_plugin_id=source_plugin_id,
        ))

    def delegate_managed_service(
        self,
        *,
        target_plugin_id: str,
        kind: str,
        variant: str,
        source_plugin_id: str,
    ) -> None:
        if target_plugin_id == source_plugin_id:
            raise ValueError(
                "delegation requires distinct target + source plugins",
            )
        self.delegated_managed_services.append(DelegatedManagedService(
            target_plugin_id=target_plugin_id,
            kind=kind,
            variant=variant,
            source_plugin_id=source_plugin_id,
        ))

    def resolve_driver(
        self,
        *,
        target_plugin_id: str,
        role: str,
    ) -> str | None:
        """Returns the source_plugin_id implementing this role
        when delegation applies, or None when the target plugin's
        own driver should be used."""
        for delegation in self.delegated_drivers:
            if (
                delegation.target_plugin_id == target_plugin_id
                and delegation.role == role
            ):
                return delegation.source_plugin_id
        return None

    def resolve_managed_service(
        self,
        *,
        target_plugin_id: str,
        kind: str,
        variant: str,
    ) -> str | None:
        for delegation in self.delegated_managed_services:
            if (
                delegation.target_plugin_id == target_plugin_id
                and delegation.kind == kind
                and delegation.variant == variant
            ):
                return delegation.source_plugin_id
        return None
