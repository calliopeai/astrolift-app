"""Keeping a tenant Temporal away from the control-plane Temporal (#1473).

Astrolift orchestrates itself on Temporal: ``TEMPORAL_ADDRESS`` /
``TEMPORAL_NAMESPACE`` in the Django settings point the control plane's own
workers at the server that carries provisioning, deprovisioning and every
other platform workflow. The tenant-facing ``workflow_engine/temporal`` driver
hands a workload the same two variables. If they ever resolve to the same
server, a tenant workload can list, describe, signal and terminate Astrolift's
own workflows, and read every other tenant's history along the way.

Ownership labels do not catch this. The control-plane Temporal is deployed by
Astrolift, so it plausibly carries ``app.kubernetes.io/managed-by: astrolift``
too; what it does not carry is a per-binding ``astrolift.io/managed-service-id``.
Ownership therefore answers "is this mine to delete", and this module answers
the different question "is this the control plane". Both have to hold.

Fail-closed means the driver refuses when it cannot *prove* separation, not
when it detects a collision. An install that has not told the driver where the
control-plane Temporal lives gets a refusal, because an unknown address cannot
be compared against anything.

Address comparison is deliberately over-eager. ``temporal-frontend:7233``,
``temporal-frontend.astrolift-system:7233`` and
``temporal-frontend.astrolift-system.svc.cluster.local:7233`` are the same
server written three ways, and a bare single-label host means "in whichever
namespace is asking", so it is treated as colliding with any host that starts
with that label. A false refusal is an operator ticket; a false clearance is a
tenant reading the control plane's histories.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_FRONTEND_PORT = 7233

_CLUSTER_DOMAIN_SUFFIXES = (".svc.cluster.local", ".svc")
_SCHEMES = ("dns:///", "dns://", "grpcs://", "grpc://", "https://", "http://")


class TemporalIsolationError(ValueError):
    """The tenant Temporal could not be proven separate from the control plane.

    A ``ValueError`` so the driver's existing validation funnel turns it into a
    non-retryable ``ProvisionResult`` / ``DeprovisionResult`` rather than a
    Temporal activity retry: no amount of retrying makes a collision go away.
    """


@dataclass(frozen=True)
class ControlPlaneTemporal:
    """Where Astrolift's own Temporal lives, as the driver must avoid it.

    Populated from the running control plane's real connection settings rather
    than from operator-supplied provider config, so the two cannot drift: the
    thing the driver refuses to collide with is by construction the thing the
    platform actually connects to.
    """

    address: str = ""
    """``host:port`` of the control-plane frontend."""

    namespaces: tuple[str, ...] = ()
    """Temporal namespaces the control plane uses."""

    kubernetes_namespaces: tuple[str, ...] = ()
    """Kubernetes namespaces that host control-plane infrastructure.

    Optional because an install may run its control plane somewhere the tenant
    cluster cannot express -- the reference AWS topology puts it on ECS Fargate
    while tenants run on EKS -- so an empty tuple is "not in this cluster"
    rather than "unknown". The address and namespace checks are the ones that
    must always be answerable.
    """

    @property
    def configured(self) -> bool:
        return bool(self.address.strip() and self.namespaces)


def normalized_host_port(address: str) -> tuple[str, int]:
    """Reduce a Temporal frontend address to a comparable ``(host, port)``."""
    value = address.strip().lower()
    for scheme in _SCHEMES:
        if value.startswith(scheme):
            value = value[len(scheme) :]
            break
    value = value.split("/", 1)[0]
    host, separator, raw_port = value.rpartition(":")
    if not separator:
        host, raw_port = value, str(DEFAULT_FRONTEND_PORT)
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise TemporalIsolationError(f"Temporal address {address!r} has a non-numeric port") from exc
    host = host.strip("[]").rstrip(".")
    for suffix in _CLUSTER_DOMAIN_SUFFIXES:
        if host.endswith(suffix):
            host = host[: -len(suffix)]
            break
    if not host:
        raise TemporalIsolationError(f"Temporal address {address!r} has no host")
    return host, port


def addresses_collide(left: str, right: str) -> bool:
    """Whether two frontend addresses may name the same server.

    Port is not part of the answer. Two Temporal frontends on one host are the
    same deployment reached two ways far more often than they are two
    independent servers, and this function exists to be wrong in the safe
    direction.
    """
    left_host, _ = normalized_host_port(left)
    right_host, _ = normalized_host_port(right)
    if left_host == right_host:
        return True
    left_labels = left_host.split(".")
    right_labels = right_host.split(".")
    if len(left_labels) == 1 or len(right_labels) == 1:
        return left_labels[0] == right_labels[0]
    return False


def assert_isolated(
    *,
    control_plane: ControlPlaneTemporal,
    kubernetes_namespace: str,
    temporal_namespace: str,
    frontend_address: str,
) -> None:
    """Refuse unless this tenant Temporal is provably not the control plane's."""
    if not control_plane.configured:
        raise TemporalIsolationError(
            "cannot prove a tenant Temporal is separate from the control-plane Temporal: "
            "the control-plane address and namespaces are not configured for this install"
        )
    if kubernetes_namespace in control_plane.kubernetes_namespaces:
        raise TemporalIsolationError(
            f"Kubernetes namespace {kubernetes_namespace!r} hosts control-plane infrastructure; "
            "a tenant Temporal may not be deployed into or addressed there"
        )
    if temporal_namespace.casefold() in {name.casefold() for name in control_plane.namespaces}:
        raise TemporalIsolationError(
            f"Temporal namespace {temporal_namespace!r} is a control-plane namespace; "
            "a tenant workflow may not address it"
        )
    if addresses_collide(frontend_address, control_plane.address):
        raise TemporalIsolationError(
            f"tenant Temporal frontend {frontend_address!r} resolves to the control-plane "
            f"Temporal at {control_plane.address!r}"
        )
