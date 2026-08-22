"""A k8s_native cluster's registry capability resolves a usable driver (#60).

``providers/k8s_native/registry_variants.py`` shipped four factories and the
availability matrix advertised the variants they build, but nothing called
them: ``driver_for_capability(cluster, "registry")`` fell through to the
cluster-level ``K8sNativeConfig``, which carries a kubeconfig path and no
registry URL, so the driver could not be constructed at all. Every assertion
here reads the resolved driver's config, which is exactly what the unwired
path could not produce.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.app_deploy import AppDeployError, driver_for_capability


def _cluster(**provider_overrides):
    provider_config = {"kubeconfig_path": "/etc/kube/config"}
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="on-prem-prod",
        region="",
        provider_config=provider_config,
        auth_config={
            "registry_username": "robot$astrolift",
            "registry_password": "s3cr3t",
        },
        provider_plugin=SimpleNamespace(slug="k8s_native"),
    )


def test_generic_oci_uses_the_declared_registry_url() -> None:
    driver = driver_for_capability(
        _cluster(oci_registry_url="https://zot.internal"),
        "registry",
    )
    assert driver._config.registry_url == "https://zot.internal"
    assert driver._config.username == "robot$astrolift"
    assert driver._config.password == "s3cr3t"


def test_generic_oci_without_a_url_says_which_key_is_missing() -> None:
    with pytest.raises(AppDeployError, match="oci_registry_url"):
        driver_for_capability(_cluster(), "registry")


def test_quay_variant_pins_quay_io_and_the_namespace() -> None:
    driver = driver_for_capability(
        _cluster(registry_variant="quay", registry_namespace="acme"),
        "registry",
    )
    assert driver._config.registry_url == "https://quay.io/acme"


def test_dockerhub_variant_pins_docker_io() -> None:
    driver = driver_for_capability(
        _cluster(registry_variant="dockerhub", registry_namespace="acme"),
        "registry",
    )
    assert driver._config.registry_url == "https://docker.io/acme"


def test_ghcr_variant_carries_the_pat_as_the_password() -> None:
    """GHCR authenticates with a PAT in the password slot, which is the whole
    reason the variant exists rather than reusing the generic driver."""
    driver = driver_for_capability(
        _cluster(registry_variant="ghcr", registry_namespace="calliopeai"),
        "registry",
    )
    assert driver._config.registry_url == "https://ghcr.io/calliopeai"
    assert driver._config.password == "s3cr3t"


def test_harbor_variant_appends_the_project_to_the_base_url() -> None:
    driver = driver_for_capability(
        _cluster(
            registry_variant="harbor",
            registry_namespace="platform",
            oci_registry_url="https://harbor.internal/",
        ),
        "registry",
    )
    assert driver._config.registry_url == "https://harbor.internal/platform"


def test_named_variant_without_a_namespace_is_refused() -> None:
    with pytest.raises(AppDeployError, match="registry_namespace"):
        driver_for_capability(_cluster(registry_variant="quay"), "registry")


def test_unknown_variant_is_refused_rather_than_silently_generic() -> None:
    with pytest.raises(AppDeployError, match="unknown registry_variant"):
        driver_for_capability(
            _cluster(registry_variant="artifactory", registry_namespace="acme"),
            "registry",
        )


def test_every_advertised_registry_variant_resolves() -> None:
    """The availability matrix is the operator-facing catalogue; a variant
    listed there and unreachable here is a deploy-time error the operator
    cannot avoid."""
    from _sdk.availability import MATRIX

    variants = [
        entry.variant
        for entry in MATRIX.drivers
        if entry.role == "registry" and entry.plugin_id == "k8s_native"
    ]
    assert variants, "matrix advertises no k8s_native registry variants"
    for variant in variants:
        cluster = _cluster(
            registry_variant=variant,
            registry_namespace="acme",
            oci_registry_url="https://harbor.internal",
        )
        driver = driver_for_capability(cluster, "registry")
        assert driver._config.registry_url, variant
