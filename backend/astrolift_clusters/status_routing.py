"""
Status service catch-all routing (#76, spec 13 §9).

Pure-Python policy. Translates 'host header → status page state'
so the status service can render the right branded page for any
unmatched host. Pairs with the ingress drivers (one each per
controller) that emit lowest-priority catch-all rules at deploy
time.

Three things this module owns:

* The **status state** vocabulary — closed enum so the status
  service templates can reliably switch on it.
* A **resolver** that takes the host + a snapshot of platform
  state (lookup_app_by_host, lookup_managed_domain_by_host) and
  returns one of the states.
* The **per-driver catch-all rule shape** — for each ingress
  driver (nginx, ALB, GCE, AGW, Gateway API), the priority /
  default-backend pattern that doesn't conflict with tenant
  ingress.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from enum import StrEnum


class StatusState(StrEnum):
    """One of these states drives the status-page template."""

    APP_NOT_FOUND = "app_not_found"
    """Host doesn't match any registered app or org domain."""

    APP_SUSPENDED = "app_suspended"
    """Org or app is suspended (billing, abuse, compliance hold)."""

    APP_DEPLOYING = "app_deploying"
    """First deploy in progress; certificate not yet issued."""

    APP_FAILED = "app_failed"
    """Most recent deployment failed; previous one (if any) is live
    but the UI still surfaces the failure state."""

    APP_NO_PUBLIC_WORKLOAD = "app_no_public_workload"
    """App is registered but has no public workload (a CronJob-only
    app, for example). Ingress shouldn't match anyway, but covers
    edge cases where someone configured a vanity domain."""

    DOMAIN_PENDING_VERIFICATION = "domain_pending_verification"
    """Org admin pointed a custom domain at the platform but
    hasn't completed the TXT/CNAME verification."""

    PLATFORM_MAINTENANCE = "platform_maintenance"
    """Operator-declared maintenance window. Highest precedence."""


@dataclasses.dataclass(frozen=True, slots=True)
class HostState:
    """Resolved result for one Host header."""

    state: StatusState
    org_slug: str = ""
    app_slug: str = ""
    detail: str = ""
    """Optional human-readable extra; the template renders verbatim."""


# ---- resolver ------------------------------------------------------


# Host-lookup callables the workflow / status service supplies.
# Returning ``None`` means 'not found in this lookup'; the resolver
# walks them in order until one matches.
HostLookupApp = Callable[[str], object | None]
HostLookupDomain = Callable[[str], object | None]


@dataclasses.dataclass(frozen=True, slots=True)
class AppLookup:
    """Minimum projection the resolver needs from an App row."""

    org_slug: str
    app_slug: str
    is_suspended: bool
    is_deploying: bool
    last_deploy_failed: bool
    has_public_workload: bool


@dataclasses.dataclass(frozen=True, slots=True)
class DomainLookup:
    """Minimum projection from a custom domain row."""

    org_slug: str
    is_verified: bool


def resolve(
    *,
    host: str,
    app_lookup: AppLookup | None,
    domain_lookup: DomainLookup | None,
    is_in_maintenance: bool = False,
) -> HostState:
    """Decide which status page to render for ``host``.

    Order of precedence (most specific / most operator-controlled
    wins):
      1. PLATFORM_MAINTENANCE — operator override; rendered before
         anything else.
      2. APP_SUSPENDED — billing / abuse holds beat normal states.
      3. APP_DEPLOYING — first deploy in progress.
      4. APP_FAILED — most recent deploy failed.
      5. APP_NO_PUBLIC_WORKLOAD — app exists but no public surface.
      6. DOMAIN_PENDING_VERIFICATION — vanity domain not yet
         verified.
      7. APP_NOT_FOUND — fallthrough.
    """
    if not host:
        # Empty host is malformed; status service still renders
        # 'not found' for clarity rather than 500ing.
        return HostState(state=StatusState.APP_NOT_FOUND)

    if is_in_maintenance:
        return HostState(
            state=StatusState.PLATFORM_MAINTENANCE,
            detail=f"platform maintenance window in effect for {host}",
        )

    if app_lookup is not None:
        if app_lookup.is_suspended:
            return HostState(
                state=StatusState.APP_SUSPENDED,
                org_slug=app_lookup.org_slug,
                app_slug=app_lookup.app_slug,
            )
        if app_lookup.is_deploying:
            return HostState(
                state=StatusState.APP_DEPLOYING,
                org_slug=app_lookup.org_slug,
                app_slug=app_lookup.app_slug,
            )
        if app_lookup.last_deploy_failed:
            return HostState(
                state=StatusState.APP_FAILED,
                org_slug=app_lookup.org_slug,
                app_slug=app_lookup.app_slug,
            )
        if not app_lookup.has_public_workload:
            return HostState(
                state=StatusState.APP_NO_PUBLIC_WORKLOAD,
                org_slug=app_lookup.org_slug,
                app_slug=app_lookup.app_slug,
            )
        # If we got here, the app is healthy and has a public
        # workload — but our catch-all ingress fired anyway. That's
        # an ingress-controller bug or a race during deploy
        # rollover. Best response is APP_DEPLOYING (likely a brief
        # gap during config reload) rather than APP_NOT_FOUND.
        return HostState(
            state=StatusState.APP_DEPLOYING,
            org_slug=app_lookup.org_slug,
            app_slug=app_lookup.app_slug,
            detail="app is healthy; ingress controller may be reloading",
        )

    if domain_lookup is not None and not domain_lookup.is_verified:
        return HostState(
            state=StatusState.DOMAIN_PENDING_VERIFICATION,
            org_slug=domain_lookup.org_slug,
        )

    return HostState(state=StatusState.APP_NOT_FOUND)


# ---- per-driver catch-all rule shape -------------------------------


class IngressDriver(StrEnum):
    NGINX = "nginx"
    AWS_ALB = "aws-alb"
    GCE = "gce"
    AZURE_AGW = "azure-agw"
    GATEWAY_API = "gateway-api"


# Each driver has a different way of expressing 'lowest priority,
# match anything not otherwise claimed'. We expose the per-driver
# *recipe* so the cluster setup workflow emits the correct manifest.
_RULE_HINTS: dict[IngressDriver, str] = {
    IngressDriver.NGINX: (
        "annotations: nginx.ingress.kubernetes.io/server-snippet "
        "with default_server; or default-backend service set on "
        "the controller."
    ),
    IngressDriver.AWS_ALB: (
        "use a default-action rule on the ALB pointing at the "
        "status-service target group; tenant rules carry higher "
        "priority numbers (lower priority value wins)."
    ),
    IngressDriver.GCE: (
        "GCE ingress requires a defaultBackend on the parent "
        "Ingress; emit one named 'astrolift-status' on the "
        "platform-namespace Ingress."
    ),
    IngressDriver.AZURE_AGW: (
        "Application Gateway: emit a default backend pool + listener with the lowest priority among rules."
    ),
    IngressDriver.GATEWAY_API: (
        "Gateway API: HTTPRoute with no hostname matchers attached "
        "to the platform Gateway; tenant HTTPRoutes pin specific "
        "hostnames so they win precedence per Gateway API spec."
    ),
}


def catch_all_recipe(driver: IngressDriver) -> str:
    """Return the documentation hint for emitting the catch-all
    rule on this ingress driver. The cluster-setup workflow uses
    these hints (translated to actual k8s manifests) when
    onboarding a new cluster."""
    return _RULE_HINTS[driver]


def supported_drivers() -> tuple[IngressDriver, ...]:
    """All drivers we know how to wire a catch-all for. Lock-tested
    so adding a new driver to ProviderPlugin requires also
    teaching this module how to emit a catch-all rule."""
    return tuple(IngressDriver)
