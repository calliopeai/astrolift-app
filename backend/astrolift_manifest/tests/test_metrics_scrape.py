"""Prometheus scrape opt-in for app workloads (#1226).

An app that exposes its own `/metrics` had no way to be scraped: the
platform rendered no discovery annotations and no monitor object, and the
cluster's Prometheus watches `PodMonitor`/`ServiceMonitor` cluster-wide
(`podMonitorSelectorNilUsesHelmValues: false`) but had nothing of the
app's to find.

Opting in is a `[workloads.<name>.metrics]` table. Opting out is the
absence of one, which is what every manifest written so far means.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.render import render_manifests
from astrolift_manifest.types import NormalizedManifest

BASE = """
astrolift_version = 1
name = "shop"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true

[[workloads.containers]]
name = "web"
port = 8080
"""

WITH_METRICS = (
    BASE
    + """
[workloads.metrics]
port = 9090
"""
)


def _render(toml_text: str):
    raw = parse_raw(toml_text)
    manifest = NormalizedManifest(
        name=raw.name,
        workloads=raw.workloads,
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )
    return render_manifests(
        manifest,
        app_slug="shop",
        namespace="astrolift-shop-prod",
        image_tag="v1",
        image_repository="ghcr.io/acme/shop",
        environment_name="production",
    )


def _by_kind(objs, kind):
    return [o for o in objs if o.get("kind") == kind]


# ---- opting out is the default -------------------------------------------


def test_a_workload_without_metrics_renders_no_monitor():
    objs = _render(BASE)
    assert _by_kind(objs, "PodMonitor") == []


def test_a_workload_without_metrics_adds_no_annotations_key():
    # Not "empty annotations" — no key at all, so this change is not a
    # diff against every pod already running.
    deployment = _by_kind(_render(BASE), "Deployment")[0]
    assert "annotations" not in deployment["spec"]["template"]["metadata"]


# ---- opting in ------------------------------------------------------------


def test_opting_in_annotates_the_pod():
    deployment = _by_kind(_render(WITH_METRICS), "Deployment")[0]
    annotations = deployment["spec"]["template"]["metadata"]["annotations"]

    assert annotations["prometheus.io/scrape"] == "true"
    assert annotations["prometheus.io/port"] == "9090"
    assert annotations["prometheus.io/path"] == "/metrics"


def test_opting_in_renders_a_pod_monitor_scoped_to_this_workload():
    monitors = _by_kind(_render(WITH_METRICS), "PodMonitor")
    assert len(monitors) == 1
    monitor = monitors[0]

    assert monitor["apiVersion"] == "monitoring.coreos.com/v1"
    assert monitor["metadata"]["namespace"] == "astrolift-shop-prod"
    assert monitor["spec"]["selector"]["matchLabels"]["astrolift.dev/workload"] == "web"
    endpoint = monitor["spec"]["podMetricsEndpoints"][0]
    assert endpoint["targetPort"] == 9090
    assert endpoint["path"] == "/metrics"


def test_the_monitor_cannot_reach_another_namespace():
    # A PodMonitor with no namespaceSelector selects across the cluster.
    # On a shared cluster that is one app scraping another's pods.
    monitor = _by_kind(_render(WITH_METRICS), "PodMonitor")[0]

    assert monitor["spec"]["namespaceSelector"] == {"matchNames": ["astrolift-shop-prod"]}


def test_a_custom_path_reaches_both_the_annotation_and_the_monitor():
    objs = _render(BASE + '\n[workloads.metrics]\nport = 9090\npath = "/internal/metrics"\n')

    deployment = _by_kind(objs, "Deployment")[0]
    assert deployment["spec"]["template"]["metadata"]["annotations"]["prometheus.io/path"] == (
        "/internal/metrics"
    )
    assert objs and _by_kind(objs, "PodMonitor")[0]["spec"]["podMetricsEndpoints"][0]["path"] == (
        "/internal/metrics"
    )


def test_enabled_false_keeps_the_config_and_stops_the_scrape():
    objs = _render(BASE + "\n[workloads.metrics]\nport = 9090\nenabled = false\n")

    assert _by_kind(objs, "PodMonitor") == []
    assert "annotations" not in _by_kind(objs, "Deployment")[0]["spec"]["template"]["metadata"]


# ---- refusals -------------------------------------------------------------


def test_metrics_without_a_port_is_refused():
    # Guessing a port scrapes whatever happens to be listening on it.
    with pytest.raises(ManifestError, match="port"):
        parse_raw(BASE + "\n[workloads.metrics]\n")


@pytest.mark.parametrize("port", ["0", "70000", '"nine thousand"'])
def test_an_impossible_port_is_refused(port):
    with pytest.raises(ManifestError, match="port"):
        parse_raw(BASE + f"\n[workloads.metrics]\nport = {port}\n")


def test_a_relative_path_is_refused():
    with pytest.raises(ManifestError, match="path"):
        parse_raw(BASE + '\n[workloads.metrics]\nport = 9090\npath = "metrics"\n')
