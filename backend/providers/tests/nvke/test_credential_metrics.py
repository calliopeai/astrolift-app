from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

import pytest

from nvke import credential_metrics as metrics
from nvke.client import Endpoint, NvkeClient, NvkeError
from nvke.contract import _contract
from tests.nvke.support import TOKEN, Peer

PREFIX = "nvke_controller_"


def sample(name: str, value: str | int, **labels: str) -> str:
    encoded = ",".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in labels.items())
    return f"{PREFIX}{name}" + (f"{{{encoded}}}" if encoded else "") + f" {value}"


def document(*rows: str) -> str:
    return "\n".join([sample("credential_metrics_collection_success", 1), *rows, ""])


def values(snapshot: metrics.CredentialMetricsSnapshot) -> dict[tuple[str, tuple[tuple[str, str], ...]], str]:
    return {(item.name, item.labels): item.value for item in snapshot.samples}


def test_catalog_pins_every_gauge_credential_state_and_source_without_mutable_aliases() -> None:
    catalog = metrics.catalog()
    assert len(catalog["metrics"]) == 18 and len(catalog["credentials"]) == 16
    assert [item["name"] for item in catalog["credentials"] if not item["required"]] == ["super-admin"]
    assert catalog["states"]["renewal_state"] == ["unknown", "accepted", "succeeded", "failed"]
    for source in (catalog["source"], catalog["credential_source"], "controller/internal/dbmetrics/dbmetrics.go"):
        assert source in _contract()["sources"]
    catalog["metrics"].clear()
    assert len(metrics.catalog()["metrics"]) == 18


def test_projection_filters_exact_cluster_and_preserves_independent_observation_facts() -> None:
    target = {"tenant_cluster": 'tenant-更新"quoted', "node": "cp-1"}
    text = document(
        sample("credential_monitoring_enabled", 0),
        sample("certificate_inspection_available", 0, **target),
        sample("certificate_observation_age_seconds", "100.123456789012345678901234567890", **target),
        sample("certificate_required", 0, **target, credential="super-admin"),
        sample("certificate_observation_available", 1, **target, credential="super-admin"),
        sample("certificate_not_after_timestamp_seconds", "1.9e9", **target, credential="super-admin"),
        sample("serving_csr_count", "9007199254740993", tenant_cluster=target["tenant_cluster"], state="pending"),
        sample("certificate_inspection_available", 1, tenant_cluster="PRIVATE_FOREIGN_CLUSTER", node="PRIVATE_NODE"),
        'unrelated_secret_metric{credential="PRIVATE_VALUE"} NaN',
    )
    result = metrics.project(text, target["tenant_cluster"])
    assert result.cluster_reported
    serialized = json.dumps(asdict(result), ensure_ascii=False)
    assert "PRIVATE" not in serialized and "tenant_cluster" not in serialized
    observed = values(result)
    assert observed["certificate_inspection_available", (("node", "cp-1"),)] == "0"
    assert observed["certificate_observation_age_seconds", (("node", "cp-1"),)] == "100.12345678901234567890123456789"
    assert (
        observed["certificate_not_after_timestamp_seconds", (("credential", "super-admin"), ("node", "cp-1"))]
        == "1900000000"
    )
    assert observed["serving_csr_count", (("state", "pending"),)] == "9007199254740993"
    assert not metrics.project(text, "tenant-更新").cluster_reported
    probe = metrics.project(text, None)
    assert not probe.cluster_reported and all(not row.labels for row in probe.samples)


@pytest.mark.parametrize(
    "row",
    [
        'certificate_inspection_available{tenant_cluster="a",node="x",node="PRIVATE"} 1',
        'certificate_inspection_available{tenant_cluster="a",node="x",secret="PRIVATE"} 1',
        'certificate_inspection_available{tenant_cluster="a",node="x"} 1 12345',
        'certificate_inspection_available{tenant_cluster="a",node="x"} NaN',
        'certificate_inspection_available{tenant_cluster="a",node="x"} 0.5',
        'certificate_inspection_available{tenant_cluster="a",node="x"} -Inf',
        'certificate_inspection_available{tenant_cluster="a",node="x"} 1e99999999',
        'certificate_observation_age_seconds{tenant_cluster="a",node="x"} -0.5',
        'certificate_not_after_timestamp_seconds{tenant_cluster="a",node="x",credential="admin"} 253402300800',
        'certificate_not_after_timestamp_seconds{tenant_cluster="a",node="x",credential="admin"} 12.5',
        'certificate_required{tenant_cluster="a",node="x",credential="super-admin"} 1',
        'certificate_required{tenant_cluster="a",node="x",credential="PRIVATE"} 1',
        'renewal_state{tenant_cluster="a",state="PRIVATE"} 1',
        'serving_csr_count{tenant_cluster="a",state="pending"} 1.5',
        'serving_csr_count{tenant_cluster="a",state="pending"} 9223372036854775808',
        'serving_csr_count{tenant_cluster="a",state="pending"} -1',
        'certificate_inspection_available{tenant_cluster="a",node="x\\nPRIVATE"} 1',
    ],
)
def test_unreviewed_or_ambiguous_known_samples_fail_without_echoing_payloads(row: str) -> None:
    with pytest.raises(NvkeError) as error:
        metrics.project(document(PREFIX + row), "a")
    assert error.value.code == "INVALID_METRICS" and "PRIVATE" not in str(error.value)


def test_duplicate_series_and_missing_inventory_marker_never_become_empty_success() -> None:
    row = sample("certificate_inspection_available", 1, tenant_cluster="a", node="x")
    with pytest.raises(NvkeError, match="pinned"):
        metrics.project(document(row, row), "a")
    with pytest.raises(NvkeError) as error:
        metrics.project(row, "a")
    assert error.value.code == "SURFACE_UNAVAILABLE"
    snapshot = metrics.project(sample("credential_metrics_collection_success", 0), "missing")
    assert not snapshot.cluster_reported and snapshot.samples[0].value == "0"


@pytest.mark.parametrize(
    "declaration",
    [
        "# TYPE nvke_controller_credential_metrics_collection_success counter",
        "# TYPE nvke_controller_credential_metrics_collection_success gauge PRIVATE",
        "# TYPE nvke_controller_credential_metrics_collection_success gauge\n"
        "# TYPE nvke_controller_credential_metrics_collection_success gauge",
    ],
)
def test_conflicting_declared_metric_type_is_not_adopted(declaration: str) -> None:
    with pytest.raises(NvkeError) as error:
        metrics.project(declaration + "\n" + document(), None)
    assert error.value.code == "INVALID_METRICS" and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("peer", [False, True], indirect=True, ids=["http", "tls"])
def test_explicit_listener_uses_only_its_credential_and_scrape_path(
    peer: Peer, caplog: pytest.LogCaptureFixture
) -> None:
    peer.headers = {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"}
    peer.body = document(sample("renewal_overdue_seconds", 0, tenant_cluster="a")).encode()
    caplog.set_level(logging.DEBUG)
    checkpoints = []
    with metrics.CredentialMetricsClient(
        peer.url + "/PRIVATE_LISTENER/metrics",
        allow_http=True,
        token=lambda: TOKEN,
        verify=peer.verify,
        before_request=lambda: checkpoints.append(True),
    ) as client:
        result = client.read("a")
    assert result.cluster_reported and len(checkpoints) == 3
    assert len(peer.requests) == 1
    assert peer.requests[0]["path"] == "/PRIVATE_LISTENER/metrics"
    assert peer.requests[0]["headers"]["Authorization"] == "Bearer " + TOKEN
    assert "version=0.0.4" in peer.requests[0]["headers"]["Accept"]
    assert "PRIVATE_LISTENER" not in caplog.text and TOKEN not in caplog.text


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500])
def test_failures_do_not_redirect_retry_or_echo_peer_responses(peer: Peer, status: int) -> None:
    peer.status, peer.body = status, b"PRIVATE_FAILURE"
    peer.headers = {"Location": peer.url + "/PRIVATE_REDIRECT"}
    with metrics.CredentialMetricsClient(peer.url, allow_http=True) as client, pytest.raises(NvkeError) as error:
        client.read("a")
    assert len(peer.requests) == 1 and error.value.status_code == status
    assert error.value.retryable is (status in {429, 500})
    assert "PRIVATE" not in str(error.value)


def test_revocation_after_scrape_prevents_snapshot_publication(peer: Peer) -> None:
    peer.headers = {"Content-Type": "text/plain"}
    peer.body = document().encode()
    calls = []

    def checkpoint() -> None:
        calls.append(True)
        if len(calls) == 3:
            raise NvkeError("SOURCE_CHANGED", "Authority changed")

    with (
        metrics.CredentialMetricsClient(peer.url, allow_http=True, before_request=checkpoint) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.read("a")
    assert len(peer.requests) == 1 and error.value.code == "SOURCE_CHANGED"


@pytest.mark.parametrize(
    "response,headers,limit,code",
    [
        (b"\xffPRIVATE", {}, 1024, "INVALID_METRICS"),
        (b"PRIVATE", {"Content-Type": "application/json"}, 1024, "INVALID_METRICS"),
        (b"PRIVATE" * 1000, {}, 32, "RESPONSE_TOO_LARGE"),
    ],
)
def test_encoding_format_and_size_are_bounded(
    peer: Peer, response: bytes, headers: dict[str, str], limit: int, code: str
) -> None:
    peer.body, peer.headers = response, headers
    with (
        metrics.CredentialMetricsClient(peer.url, allow_http=True, max_bytes=limit) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.read("a")
    assert error.value.code == code and "PRIVATE" not in str(error.value)


def test_transport_failure_is_safe_and_attempted_once(peer: Peer) -> None:
    peer.disconnect = True
    with metrics.CredentialMetricsClient(peer.url, allow_http=True) as client, pytest.raises(NvkeError) as error:
        client.read("a")
    assert error.value.code == "TRANSPORT_ERROR" and len(peer.requests) == 1


def test_sample_budget_fails_instead_of_truncating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics, "MAX_SAMPLES", 1)
    with pytest.raises(NvkeError) as error:
        metrics.project(document(sample("credential_monitoring_enabled", 1)), None)
    assert error.value.code == "OBSERVATION_LIMIT"


@pytest.mark.parametrize("definition", metrics.catalog()["metrics"], ids=lambda row: row["name"])
def test_every_pinned_gauge_is_projected(definition: dict[str, Any]) -> None:
    labels = {
        name: {"tenant_cluster": "a", "node": "node-a", "credential": "admin", "state": "failed"}[name]
        for name in definition["labels"]
    }
    name = definition["name"]
    text = sample(name, 1, **labels)
    if name != "credential_metrics_collection_success":
        text = document(text)
    snapshot = metrics.project(text, "a")
    assert any(row.name == name and row.value == "1" for row in snapshot.samples)


def test_private_http_suppresses_native_spans_and_restores_unrelated_telemetry(
    peer: Peer, caplog: pytest.LogCaptureFixture
) -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    peer.headers, peer.body = {"Content-Type": "text/plain"}, document().encode()
    caplog.set_level(logging.DEBUG)
    try:
        with metrics.CredentialMetricsClient(
            peer.url + "/PRIVATE_ENDPOINT", allow_http=True, token=lambda: TOKEN
        ) as client:
            HTTPXClientInstrumentor.instrument_client(client._http, tracer_provider=provider)
            client.read("a")
            assert not exporter.get_finished_spans()
            assert "PRIVATE_ENDPOINT" not in caplog.text and TOKEN not in caplog.text
            client._http.get(peer.url + "/public-proof")
            assert "public-proof" in caplog.text
        assert len(exporter.get_finished_spans()) == 1
        assert "PRIVATE_ENDPOINT" not in str(exporter.get_finished_spans())
    finally:
        provider.shutdown()


def test_absolute_scrape_deadline_cannot_be_extended_by_successful_reads(
    peer: Peer, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([0.0, 31.0])
    monkeypatch.setattr(metrics, "monotonic", lambda: next(clock))
    peer.headers, peer.body = {"Content-Type": "text/plain"}, document().encode()
    with (
        metrics.CredentialMetricsClient(peer.url, allow_http=True, timeout=30) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.read("a")
    assert error.value.code == "TRANSPORT_ERROR" and len(peer.requests) == 1


def test_existing_rest_transport_keeps_private_native_filters_out_of_logs_and_spans(
    peer: Peer, caplog: pytest.LogCaptureFixture
) -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider, exporter = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    caplog.set_level(logging.DEBUG)
    try:
        with NvkeClient(
            [Endpoint("controller", peer.url + "/PRIVATE_API", token=lambda: TOKEN, allow_http=True)]
        ) as client:
            HTTPXClientInstrumentor.instrument_client(client._http, tracer_provider=provider)
            client.execute("listClusters", query={"name": "PRIVATE_FILTER"})
        assert "PRIVATE" not in caplog.text and TOKEN not in caplog.text
        assert not exporter.get_finished_spans()
    finally:
        provider.shutdown()


def test_private_http_excludes_sentry_spans_without_disabling_the_parent_transaction(peer: Peer) -> None:
    import sentry_sdk
    from sentry_sdk.integrations.httpx import HttpxIntegration
    from sentry_sdk.transport import Transport

    captured = []

    class Sink(Transport):
        def capture_envelope(self, envelope: Any) -> None:
            captured.append(envelope)

    owned = sentry_sdk.Client(
        dsn="https://test@example.test/1",
        transport=Sink(),
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[HttpxIntegration()],
        traces_sample_rate=1,
    )
    peer.headers, peer.body = {"Content-Type": "text/plain"}, document().encode()
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_client(owned)
            with (
                sentry_sdk.start_transaction(name="owned-qualification", sampled=True),
                metrics.CredentialMetricsClient(
                    peer.url + "/PRIVATE_ENDPOINT", allow_http=True, token=lambda: TOKEN
                ) as client,
            ):
                client.read("a")
                assert sentry_sdk.get_client() is owned
                client._http.get(peer.url + "/public-proof")
        owned.flush()
        events = [
            json.loads(item.get_bytes())
            for envelope in captured
            for item in envelope.items
            if item.headers.get("type") == "transaction"
        ]
        assert len(events) == 1
        encoded = json.dumps(events)
        assert "public-proof" in encoded and "PRIVATE_ENDPOINT" not in encoded and TOKEN not in encoded
    finally:
        owned.close()
