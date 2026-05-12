"""Tests for ingress mode + routing policy (#64, spec 13 §4)."""

from __future__ import annotations

import pytest

from astrolift_clusters.ingress_modes import (
    RoutingError,
    per_app_annotations,
    resolve_routing,
    shared_annotations_for,
    validate_expose_paths,
)
from astrolift_clusters.status_routing import IngressDriver

# ---- shared mode annotations ---------------------------------------


def test_shared_alb_uses_group_annotations():
    out = shared_annotations_for(
        driver=IngressDriver.AWS_ALB,
        org_slug="acme",
        app_slug="api",
    )
    assert out.group_name_key.endswith("group.name")
    assert out.group_name_value == "astrolift-acme"
    assert out.group_order_value == "100"


def test_shared_group_namespaced_by_org():
    """Two orgs in the same cluster get separate ALB groups
    (separate LBs) even in shared_ingress mode — multi-tenant
    safety default."""
    a = shared_annotations_for(
        driver=IngressDriver.AWS_ALB,
        org_slug="acme",
        app_slug="api",
    )
    b = shared_annotations_for(
        driver=IngressDriver.AWS_ALB,
        org_slug="other-org",
        app_slug="api",
    )
    assert a.group_name_value != b.group_name_value


def test_shared_nginx_no_order_annotation():
    """nginx-ingress doesn't use group ordering."""
    out = shared_annotations_for(
        driver=IngressDriver.NGINX,
        org_slug="acme",
        app_slug="api",
    )
    assert out.group_order_value == ""


def test_per_app_unique_group_per_app():
    """Per-app mode = one LB per app. Group name distinguishes
    apps within the same org."""
    a = per_app_annotations(driver=IngressDriver.AWS_ALB, app_slug="api")
    b = per_app_annotations(driver=IngressDriver.AWS_ALB, app_slug="web")
    assert a.group_name_value != b.group_name_value
    assert "api" in a.group_name_value
    assert "web" in b.group_name_value


def test_unsupported_driver_raises():
    with pytest.raises(ValueError):
        shared_annotations_for(
            driver="bogus",  # type: ignore[arg-type]
            org_slug="acme",
            app_slug="api",
        )


# ---- path validation -----------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/api",
        "/api/v1",
        "/api/v1/users",
        "/api/v1/*",
        "/.well-known/something",
        "/api-v2_beta/",
    ],
)
def test_valid_paths(path):
    validate_expose_paths([path])


@pytest.mark.parametrize(
    "bad_path",
    [
        "api/v1",  # missing leading /
        "/api with space",
        "/api?query=1",  # query string not allowed
        "/api#frag",
        "/api/(.*)",  # regex chars not allowed
    ],
)
def test_invalid_paths_rejected(bad_path):
    with pytest.raises(RoutingError):
        validate_expose_paths([bad_path])


def test_empty_paths_rejected_when_set():
    """Empty list when expose_paths IS set is a misconfig."""
    with pytest.raises(RoutingError, match="must not be empty"):
        validate_expose_paths([])


def test_non_string_path_rejected():
    with pytest.raises(RoutingError, match="must be a string"):
        validate_expose_paths([123])  # type: ignore[list-item]


# ---- routing resolution --------------------------------------------


def test_default_is_host_routing():
    out = resolve_routing(
        app_hostname="api.acme.com",
        workload_hostname="api.acme.com",
    )
    assert out.mode == "host"
    assert out.hostname == "api.acme.com"
    assert out.paths == ()


def test_workload_hostname_wins_when_set():
    """A workload can declare its own hostname for host-mode
    (different workloads → different subdomains)."""
    out = resolve_routing(
        app_hostname="acme.com",
        workload_hostname="api.acme.com",
    )
    assert out.hostname == "api.acme.com"


def test_path_mode_when_paths_set():
    out = resolve_routing(
        app_hostname="api.acme.com",
        workload_hostname="",
        expose_paths=["/api/v1/*"],
    )
    assert out.mode == "path"
    assert out.hostname == "api.acme.com"
    assert out.paths == ("/api/v1/*",)


def test_path_mode_rejects_different_host():
    """Spec rule: path-mode requires the workload to share the
    parent app's hostname. Operator declaring both is a contradiction."""
    with pytest.raises(RoutingError, match="path-mode requires shared"):
        resolve_routing(
            app_hostname="api.acme.com",
            workload_hostname="other.acme.com",
            expose_paths=["/api/v1/*"],
        )


def test_path_mode_with_matching_workload_host_passes():
    """Workload explicitly setting workload_hostname == app_hostname
    is fine."""
    out = resolve_routing(
        app_hostname="api.acme.com",
        workload_hostname="api.acme.com",
        expose_paths=["/api/v1/*"],
    )
    assert out.mode == "path"


def test_path_mode_validates_paths():
    """Bad paths bubble up from validate_expose_paths."""
    with pytest.raises(RoutingError):
        resolve_routing(
            app_hostname="api.acme.com",
            workload_hostname="",
            expose_paths=["bad-no-slash"],
        )
