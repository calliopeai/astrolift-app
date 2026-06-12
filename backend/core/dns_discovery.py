"""Driver-keyed DNS zone + certificate discovery for the managed-domain
picker (#861 / #858).

The managed-domain "Add domain" dialog is *not* bound to a cluster — the
operator picks a DNS driver (``route53`` / ``cloud_dns`` / ``azure_dns``)
and a zone before any cluster is in the loop. So unlike
``core.app_deploy.driver_for_capability`` (which resolves a driver from a
``TenantCluster`` row), this module resolves a standalone driver instance
keyed only by the DNS-driver slug and lists zones / certs through the
platform's ambient cloud credentials — the same credential model the
wildcard-cert provisioning path already relies on (boto3 default chain on
AWS).

Only ``route53`` is wired today; ``cloud_dns`` / ``azure_dns`` report
``supported=False`` with an empty list until their list APIs land, which
the UI renders as "zone discovery not yet supported on this driver"
(manual textarea entry remains available).

Tests inject a fake DNS driver via :func:`set_dns_driver_for_tests` so the
moto-backed cases don't depend on the registry being loaded.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# DNS-driver slugs that have zone/cert discovery wired. Slugs absent from
# this set return ``supported=False`` — the picker degrades to manual
# entry rather than erroring.
_SUPPORTED_DNS_DRIVERS = frozenset({"route53"})


# ---- Test-injectable driver --------------------------------------


_DNS_DRIVER_OVERRIDE: Any = None


def set_dns_driver_for_tests(driver: Any) -> None:
    """Install a fake DNS driver so discovery tests run without the
    provider registry or live cloud creds. The fake only needs the
    ``list_zones`` / ``list_certificates`` methods. Reset with
    :func:`reset_dns_driver_for_tests`."""
    global _DNS_DRIVER_OVERRIDE
    _DNS_DRIVER_OVERRIDE = driver


def reset_dns_driver_for_tests() -> None:
    global _DNS_DRIVER_OVERRIDE
    _DNS_DRIVER_OVERRIDE = None


def _driver_for_dns_slug(dns_driver: str) -> Any | None:
    """Resolve a standalone DNS driver instance for ``dns_driver``.

    Returns ``None`` for an unsupported / unknown slug so callers report
    ``supported=False``. The test override short-circuits the lookup so
    fixtures don't need the registry loaded.
    """
    if _DNS_DRIVER_OVERRIDE is not None:
        return _DNS_DRIVER_OVERRIDE
    if dns_driver not in _SUPPORTED_DNS_DRIVERS:
        return None
    if dns_driver == "route53":
        # Route53Config defaults to us-east-1, which is also the correct
        # region for ACM DNS-validated certs (CloudFront / global ALB),
        # so the cert picker reads the same region the wildcard-cert
        # provisioning path uses. Credentials come from the boto3 default
        # chain (the platform's IAM role) — same as request_wildcard_cert.
        from aws.dns_route53 import Route53Config, Route53Driver

        return Route53Driver(config=Route53Config())
    return None


# ---- Public dispatch ----------------------------------------------


def dns_zones_dispatch(*, dns_driver: str) -> dict[str, Any]:
    """Return discoverable DNS zones for ``dns_driver`` (#861).

    Returns::

        {"supported": bool, "zones": [{"id", "name", "private",
          "config_json"}, ...]}

    ``supported`` is ``False`` (empty list) for drivers without zone
    discovery wired. A supported driver that can't reach its cloud API
    (no creds, throttled) stays ``supported=True`` with an empty list so
    the UI keeps the picker's empty state rather than reverting to
    manual entry. ``config_json`` is a pre-serialized JSON string ready
    to drop into the dialog's DNS-config textarea.
    """
    driver = _driver_for_dns_slug(dns_driver)
    if driver is None or not hasattr(driver, "list_zones"):
        return {"supported": False, "zones": []}
    try:
        zones = driver.list_zones()
    except Exception as exc:  # noqa: BLE001
        log.warning("dns_zones_dispatch: list_zones failed driver=%s: %s", dns_driver, exc)
        return {"supported": True, "zones": []}
    return {
        "supported": True,
        "zones": [
            {
                "id": z.get("id", ""),
                "name": z.get("name", ""),
                "private": bool(z.get("private", False)),
                "config_json": z.get("config_json", ""),
            }
            for z in zones
        ],
    }


def dns_certificates_dispatch(*, dns_driver: str) -> dict[str, Any]:
    """Return discoverable TLS certificates for ``dns_driver`` (#858).

    Driver-keyed analog of ``cluster_certificates_dispatch`` for the
    managed-domain dialog, which has no cluster context. Returns::

        {"supported": bool, "certificates": [{"arn", "name",
          "domain_name", "status"}, ...]}

    Same ``supported`` semantics as :func:`dns_zones_dispatch`. For
    ``route53`` the certs come from the region-scoped ACM client.
    """
    driver = _driver_for_dns_slug(dns_driver)
    if driver is None or not hasattr(driver, "list_certificates"):
        return {"supported": False, "certificates": []}
    try:
        certs = driver.list_certificates()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "dns_certificates_dispatch: list_certificates failed driver=%s: %s",
            dns_driver,
            exc,
        )
        return {"supported": True, "certificates": []}
    return {
        "supported": True,
        "certificates": [
            {
                "arn": c.get("arn", ""),
                "name": c.get("name", ""),
                "domain_name": c.get("domain_name", ""),
                "status": c.get("status", ""),
            }
            for c in certs
        ],
    }


__all__ = [
    "dns_certificates_dispatch",
    "dns_zones_dispatch",
    "reset_dns_driver_for_tests",
    "set_dns_driver_for_tests",
]
