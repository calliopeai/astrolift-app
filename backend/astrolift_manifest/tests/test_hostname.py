"""Tests for hostname computation (#118)."""

from __future__ import annotations

import pytest

from astrolift_manifest.hostname import (
    HostnameInputs,
    compute_hostnames,
    compute_preview_hostname,
)
from astrolift_manifest.types import (
    ContainerManifest,
    NormalizedManifest,
    WorkloadManifest,
)


def _normalized(*workloads: WorkloadManifest) -> NormalizedManifest:
    return NormalizedManifest(
        name="hello",
        workloads=workloads,
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


def _wl(slug: str, *, is_public: bool, kind: str = "deployment") -> WorkloadManifest:
    return WorkloadManifest(
        name=slug,
        kind=kind,
        is_public=is_public,
        containers=(ContainerManifest(name="app", is_primary=True, port=8080),),
    )


def _wl_with_slug(slug: str, **kwargs) -> WorkloadManifest:
    """WorkloadManifest carries name only — there is no slug column.
    The hostname module reads ``w.slug`` though; use a slug attr via
    a subclass-style dataclass replace."""
    return _wl(slug, **kwargs)


# ---- zero / one / many public workloads -------------------------------


def test_no_public_workloads_yields_no_hostnames():
    manifest = _normalized(_wl("web", is_public=False))
    assert (
        compute_hostnames(
            manifest,
            HostnameInputs(app_slug="hello", org_slug="acme", base_zone="astrolift.dev"),
        )
        == []
    )


def test_single_public_workload_uses_app_slug():
    manifest = _normalized(_wl("web", is_public=True))
    out = compute_hostnames(
        manifest,
        HostnameInputs(app_slug="hello", org_slug="acme", base_zone="astrolift.dev"),
    )
    assert len(out) == 1
    assert out[0].hostname == "hello.acme.astrolift.dev"


def test_subdomain_override_takes_precedence():
    manifest = _normalized(_wl("web", is_public=True))
    out = compute_hostnames(
        manifest,
        HostnameInputs(
            app_slug="hello",
            org_slug="acme",
            base_zone="astrolift.dev",
            subdomain_override="custom",
        ),
    )
    assert out[0].hostname == "custom.acme.astrolift.dev"


def test_multi_public_workloads_get_flat_suffix():
    """Two public workloads → both get -<slug> suffix so a single
    wildcard cert (*.acme.astrolift.dev) covers them."""
    manifest = _normalized(
        _wl("web", is_public=True),
        _wl("api", is_public=True),
    )
    out = compute_hostnames(
        manifest,
        HostnameInputs(app_slug="hello", org_slug="acme", base_zone="astrolift.dev"),
    )
    hosts = sorted(o.hostname for o in out)
    assert hosts == [
        "hello-api.acme.astrolift.dev",
        "hello-web.acme.astrolift.dev",
    ]


def test_multi_workload_with_one_private_skips_the_private():
    manifest = _normalized(
        _wl("web", is_public=True),
        _wl("worker", is_public=False),
        _wl("api", is_public=True),
    )
    out = compute_hostnames(
        manifest,
        HostnameInputs(app_slug="hello", org_slug="acme", base_zone="astrolift.dev"),
    )
    assert {o.workload_slug for o in out} == {"web", "api"}


# ---- subdomain validation ---------------------------------------------


def test_invalid_subdomain_override_rejected():
    """Bad DNS labels would emit broken hostnames; reject up front."""
    manifest = _normalized(_wl("web", is_public=True))
    with pytest.raises(ValueError, match="DNS label"):
        compute_hostnames(
            manifest,
            HostnameInputs(
                app_slug="hello",
                org_slug="acme",
                base_zone="astrolift.dev",
                subdomain_override="-bad-",
            ),
        )


def test_uppercase_subdomain_normalized_to_lower():
    manifest = _normalized(_wl("web", is_public=True))
    out = compute_hostnames(
        manifest,
        HostnameInputs(app_slug="HELLO", org_slug="acme", base_zone="astrolift.dev"),
    )
    assert out[0].hostname == "hello.acme.astrolift.dev"


# ---- preview ----------------------------------------------------------


def test_preview_hostname_pattern():
    h = compute_preview_hostname(pr_number=42, app_slug="hello", org_slug="acme", base_zone="astrolift.dev")
    assert h == "pr-42-hello.pr.acme.astrolift.dev"


def test_preview_hostname_rejects_non_positive_pr_number():
    with pytest.raises(ValueError):
        compute_preview_hostname(pr_number=0, app_slug="x", org_slug="y", base_zone="z")
