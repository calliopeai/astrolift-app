"""Tests for status service catch-all routing (#76, spec 13 §9)."""

from __future__ import annotations

import pytest

from astrolift_clusters.status_routing import (
    AppLookup,
    DomainLookup,
    IngressDriver,
    StatusState,
    catch_all_recipe,
    resolve,
    supported_drivers,
)


def _app(**kw) -> AppLookup:
    base = dict(
        org_slug="acme",
        app_slug="api",
        is_suspended=False,
        is_deploying=False,
        last_deploy_failed=False,
        has_public_workload=True,
    )
    base.update(kw)
    return AppLookup(**base)


# ---- precedence ----------------------------------------------------


def test_maintenance_beats_everything():
    out = resolve(
        host="api.acme.com",
        app_lookup=_app(is_suspended=True),  # would be APP_SUSPENDED otherwise
        domain_lookup=None,
        is_in_maintenance=True,
    )
    assert out.state == StatusState.PLATFORM_MAINTENANCE


def test_suspended_beats_deploying():
    out = resolve(
        host="api.acme.com",
        app_lookup=_app(is_suspended=True, is_deploying=True),
        domain_lookup=None,
    )
    assert out.state == StatusState.APP_SUSPENDED


def test_deploying_beats_failed():
    """Active in-progress deploy is the user-facing signal."""
    out = resolve(
        host="api.acme.com",
        app_lookup=_app(is_deploying=True, last_deploy_failed=True),
        domain_lookup=None,
    )
    assert out.state == StatusState.APP_DEPLOYING


def test_failed_state_for_failed_deploys():
    out = resolve(
        host="api.acme.com",
        app_lookup=_app(last_deploy_failed=True),
        domain_lookup=None,
    )
    assert out.state == StatusState.APP_FAILED


def test_no_public_workload_for_cron_only_app():
    out = resolve(
        host="api.acme.com",
        app_lookup=_app(has_public_workload=False),
        domain_lookup=None,
    )
    assert out.state == StatusState.APP_NO_PUBLIC_WORKLOAD


def test_healthy_app_returns_deploying_during_reload():
    """If the catch-all fires for a healthy app, it's almost
    certainly an ingress-controller config reload race. Show
    APP_DEPLOYING instead of APP_NOT_FOUND so the user gets
    a sensible message during the brief gap."""
    out = resolve(
        host="api.acme.com",
        app_lookup=_app(),  # all-healthy
        domain_lookup=None,
    )
    assert out.state == StatusState.APP_DEPLOYING
    assert "ingress controller" in out.detail


def test_unverified_custom_domain():
    out = resolve(
        host="myapp.acme.com",
        app_lookup=None,
        domain_lookup=DomainLookup(org_slug="acme", is_verified=False),
    )
    assert out.state == StatusState.DOMAIN_PENDING_VERIFICATION
    assert out.org_slug == "acme"


def test_verified_custom_domain_falls_through_to_not_found():
    """Verified domain but no app match → still not found.
    Verification just unlocks routing; the app must exist too."""
    out = resolve(
        host="myapp.acme.com",
        app_lookup=None,
        domain_lookup=DomainLookup(org_slug="acme", is_verified=True),
    )
    assert out.state == StatusState.APP_NOT_FOUND


def test_app_not_found_for_unmatched_host():
    out = resolve(host="random.example", app_lookup=None, domain_lookup=None)
    assert out.state == StatusState.APP_NOT_FOUND


def test_empty_host_renders_not_found():
    """Defensive: an empty host header is malformed but the status
    service should still render a sensible page."""
    out = resolve(host="", app_lookup=None, domain_lookup=None)
    assert out.state == StatusState.APP_NOT_FOUND


def test_resolve_carries_org_and_app_slugs():
    out = resolve(
        host="api.acme.com",
        app_lookup=_app(is_suspended=True),
        domain_lookup=None,
    )
    assert out.org_slug == "acme"
    assert out.app_slug == "api"


# ---- ingress driver recipes ----------------------------------------


def test_supported_drivers_locked():
    """Adding a new ProviderPlugin must include teaching this
    module how to emit a catch-all rule on the new ingress
    driver. The locked tuple catches new drivers in code review."""
    assert supported_drivers() == (
        IngressDriver.NGINX,
        IngressDriver.AWS_ALB,
        IngressDriver.GCE,
        IngressDriver.AZURE_AGW,
        IngressDriver.GATEWAY_API,
    )


@pytest.mark.parametrize("driver", list(IngressDriver))
def test_every_driver_has_a_recipe(driver):
    recipe = catch_all_recipe(driver)
    assert recipe and len(recipe) > 20  # something specific


def test_alb_recipe_mentions_priority():
    """ALB recipes must talk about priority because tenant rules
    would otherwise win by accident."""
    assert "priority" in catch_all_recipe(IngressDriver.AWS_ALB)


def test_gateway_api_recipe_mentions_hostname_matcher():
    """Gateway API uses hostname matcher precedence — the recipe
    must call this out, not say 'priority'."""
    assert "hostname" in catch_all_recipe(IngressDriver.GATEWAY_API)
