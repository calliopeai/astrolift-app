from __future__ import annotations

from dataclasses import asdict
from unittest.mock import patch

import httpx
import pytest

from nvke.client import Endpoint, NvkeError
from nvke.infrastructure import GROUP, providers
from nvke.infrastructure_client import METADATA_ACCEPT, InfrastructureClient
from tests.nvke.test_infrastructure import NOW_NS


class Kubernetes:
    def __init__(self):
        self.requests = []
        self.overrides = {}
        self.deployments = [self.manager()]
        self.identities = [
            {
                "metadata": {
                    "name": "nico-default",
                    "namespace": "capnico-system",
                    "generation": 1,
                    "creationTimestamp": "2026-11-08T04:00:00Z",
                },
                "spec": {"credentialsRef": {"name": "nico-credentials"}},
                "status": {
                    "lastCheckedTime": "2026-11-08T05:59:00Z",
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "True",
                            "observedGeneration": 1,
                            "message": "PRIVATE_PROVIDER_MESSAGE",
                        }
                    ],
                },
            }
        ]

    @staticmethod
    def manager(provider="nico", *, generation=1, observed=1, available="True", ready=1, args=None):
        p = next(p for p in providers() if p.type == provider)
        return {
            "metadata": {
                "name": p.controller_deployment,
                "namespace": p.controller_namespace,
                "generation": generation,
                "labels": {"app.kubernetes.io/name": p.app_name},
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"args": args or []}, {"args": ["--irrelevant-token=PRIVATE_MANAGER_TOKEN"]}]
                    }
                }
            },
            "status": {
                "observedGeneration": observed,
                "readyReplicas": ready,
                "replicas": 1,
                "conditions": [{"type": "Available", "status": available}],
            },
        }

    def handle(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        path = request.url.path
        if path in self.overrides:
            result = self.overrides[path]
            return result(request) if callable(result) else result
        if path == "/apis/apps/v1/deployments":
            assert request.url.params["labelSelector"] == "app.kubernetes.io/name"
            return httpx.Response(200, json={"metadata": {"resourceVersion": "17"}, "items": self.deployments})
        if "/customresourcedefinitions/" in path:
            return httpx.Response(
                200,
                json={
                    "metadata": {"name": path.rsplit("/", 1)[1]},
                    "spec": {"versions": [{"served": True}]},
                    "status": {"conditions": [{"type": "Established", "status": "True"}]},
                },
            )
        if "/secrets/" in path:
            assert request.headers["accept"] == METADATA_ACCEPT
            namespace, name = path.split("/")[4], path.split("/")[6]
            return httpx.Response(
                200,
                json={
                    "apiVersion": "meta.k8s.io/v1",
                    "kind": "PartialObjectMetadata",
                    "metadata": {"name": name, "namespace": namespace},
                },
            )
        if path.endswith("/nicoidentities"):
            return httpx.Response(200, json={"metadata": {"resourceVersion": "18"}, "items": self.identities})
        raise AssertionError("unexpected management request")

    def inspect(self, **kwargs):
        with (
            InfrastructureClient(
                Endpoint("datastore", "https://kubernetes.example.test"),
                transport=httpx.MockTransport(self.handle),
                **kwargs,
            ) as client,
            patch("nvke.infrastructure_client.time.time_ns", return_value=NOW_NS),
        ):
            return client.inspect()


@pytest.fixture
def kube():
    return Kubernetes()


def test_complete_inspection_preserves_separate_configuration_credential_and_launchlayer_states(kube):
    result = kube.inspect()
    nico, launch = result.providers
    assert result.complete
    assert nico.state == "CONFIGURED" and nico.credentials.health == "HEALTHY"
    assert nico.secret_present is True and nico.ready_replicas == 1
    assert launch.state == "NOT-CONFIGURED" and launch.unconfigured_is_valid and launch.credentials is None
    assert "PRIVATE" not in repr(asdict(result))
    assert result.observed_at == "2026-11-08T06:00:00.000000000Z"
    assert all(r.url.host == "kubernetes.example.test" for r in kube.requests)


@pytest.mark.parametrize("surface", ["controller", "operator", "tenant"])
def test_no_public_api_or_fallback_connection_can_inspect_infrastructure(surface):
    with pytest.raises(ValueError, match="explicit management"):
        InfrastructureClient(Endpoint(surface, "https://unused.example.test"))


@pytest.mark.parametrize(
    ("status", "state", "present"), [(404, "NOT-CONFIGURED", False), (403, "UNKNOWN", None), (500, "UNKNOWN", None)]
)
def test_absent_unreadable_and_failed_secret_probes_are_not_conflated(kube, status, state, present):
    path = "/api/v1/namespaces/capnico-system/secrets/nico-credentials"
    kube.overrides[path] = httpx.Response(status, json={"message": "PRIVATE_NATIVE_FAILURE"})
    result = kube.inspect().providers[0]
    assert result.state == state and result.secret_present is present
    assert result.credentials.health == "HEALTHY"
    assert result.complete == (status == 404)
    assert len([r for r in kube.requests if r.url.path == path]) == 1
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"apiVersion": "v1", "kind": "Secret", "data": {"token": "PRIVATE_SECRET_VALUE"}},
        {
            "apiVersion": "meta.k8s.io/v1",
            "kind": "PartialObjectMetadata",
            "metadata": {"name": "other", "namespace": "capnico-system"},
        },
        {
            "apiVersion": "meta.k8s.io/v1",
            "kind": "PartialObjectMetadata",
            "metadata": {"name": "nico-credentials", "namespace": "capnico-system"},
            "stringData": {"token": "PRIVATE_SECRET_VALUE"},
        },
    ],
)
def test_secret_values_or_mismatched_metadata_are_refused_without_retry_or_public_projection(kube, payload):
    path = "/api/v1/namespaces/capnico-system/secrets/nico-credentials"
    kube.overrides[path] = httpx.Response(200, json=payload)
    result = kube.inspect().providers[0]
    assert result.state == "UNKNOWN" and not result.complete
    assert len([r for r in kube.requests if r.url.path == path]) == 1
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize(
    ("status", "health", "reason"),
    [
        (404, "UNSUPPORTED", "identity_api_not_served"),
        (403, "UNREADABLE", "identity_list_forbidden"),
        (500, "UNREADABLE", "identity_list_unavailable"),
    ],
)
def test_identity_failure_is_independent_of_provider_configuration(kube, status, health, reason):
    kube.overrides[f"/apis/{GROUP}/v1alpha1/namespaces/capnico-system/nicoidentities"] = httpx.Response(status)
    result = kube.inspect().providers[0]
    assert result.state == "CONFIGURED" and result.credentials.health == health
    assert result.credentials.reason == reason and not result.complete


@pytest.mark.parametrize(
    ("field", "value"), [("observedGeneration", 0), ("observedGeneration", True), ("observedGeneration", 0.5)]
)
def test_old_or_malformed_manager_generation_cannot_report_configuration_ready(kube, field, value):
    kube.deployments[0]["status"][field] = value
    result = kube.inspect().providers[0]
    assert result.state == "DEGRADED" and result.controller_available is False
    assert result.credentials.health == "HEALTHY"


def test_launchlayer_requires_explicit_endpoint_when_credentials_exist_and_preserves_optional_unauthenticated_mode(
    kube,
):
    kube.deployments.append(kube.manager("launchlayer", args=["--identity-secret", "ll-credentials"]))
    result = kube.inspect().providers[1]
    assert result.state == "DEGRADED" and result.reason == "provider_endpoint_absent"
    kube.deployments[1] = kube.manager(
        "launchlayer", args=["--identity-secret=ll-credentials", "--base-url=https://PRIVATE:SECRET@ll.example.test"]
    )
    result = kube.inspect().providers[1]
    assert result.state == "CONFIGURED" and result.endpoint_configured
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize("kind", ["absent", "unserved", "unestablished", "deleting", "forbidden"])
def test_crd_state_does_not_invent_installed_or_healthy_provider(kube, kind):
    path = "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/" + providers()[0].crds[0]
    document = {
        "metadata": {"name": providers()[0].crds[0]},
        "spec": {"versions": [{"served": True}]},
        "status": {"conditions": [{"type": "Established", "status": "True"}]},
    }
    status = 200
    if kind == "absent":
        status = 404
    elif kind == "forbidden":
        status = 403
    elif kind == "deleting":
        document["metadata"]["deletionTimestamp"] = "2026-11-08T06:00:00Z"
    elif kind == "unserved":
        document["spec"]["versions"][0]["served"] = False
    else:
        document["status"]["conditions"][0]["status"] = "False"
    kube.overrides[path] = httpx.Response(status, json=document)
    result = kube.inspect().providers[0]
    assert result.state == ("UNKNOWN" if kind == "forbidden" else "NOT-INSTALLED")
    assert result.credentials is None
    assert not any("/secrets/" in r.url.path for r in kube.requests)


@pytest.mark.parametrize("failure", ["repeated", "version", "duplicate", "malformed"])
def test_pagination_anomalies_cannot_become_complete_inventory(kube, failure):
    def page(request):
        token = request.url.params.get("continue")
        version = "second" if token and failure == "version" else "17"
        items = kube.deployments if not token or failure == "duplicate" else []
        if failure == "malformed":
            items = [None]
        return httpx.Response(200, json={"metadata": {"resourceVersion": version, "continue": "next"}, "items": items})

    kube.overrides["/apis/apps/v1/deployments"] = page
    result = kube.inspect()
    assert not result.complete and all(p.state == "UNKNOWN" for p in result.providers)
    assert not any("/secrets/" in r.url.path for r in kube.requests)


def test_later_manager_page_is_not_silently_lost(kube):
    def page(request):
        second = bool(request.url.params.get("continue"))
        return httpx.Response(
            200,
            json={
                "metadata": {"resourceVersion": "17", "continue": "" if second else "next"},
                "items": kube.deployments if second else [],
            },
        )

    kube.overrides["/apis/apps/v1/deployments"] = page
    assert kube.inspect().providers[0].state == "CONFIGURED"


def test_revocation_after_response_prevents_any_followup_read(kube):
    def check():
        if kube.requests:
            raise NvkeError("PERMISSION_DENIED", "synthetic revoked authority")

    with pytest.raises(NvkeError) as error:
        kube.inspect(before_request=check)
    assert error.value.code == "PERMISSION_DENIED" and len(kube.requests) == 1


def test_total_response_budget_stops_inspection_without_publishing_partial_success(kube):
    with patch("nvke.infrastructure_client.MAX_OBSERVATION_BYTES", 10), pytest.raises(NvkeError) as error:
        kube.inspect()
    assert error.value.code == "OBSERVATION_LIMIT" and len(kube.requests) == 1


def test_page_budget_is_bounded_and_unreadable(kube):
    def page(request):
        return httpx.Response(200, json={"metadata": {"resourceVersion": "17", "continue": "next"}, "items": []})

    kube.overrides["/apis/apps/v1/deployments"] = page
    with patch("nvke.infrastructure_client.MAX_PAGES", 1):
        result = kube.inspect()
    assert not result.complete and all(p.state == "UNKNOWN" for p in result.providers)


def test_deadline_is_enforced_on_raw_chunks_before_small_chunks_can_accumulate(kube):
    class Dribble(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{"
            clock[0] += 31
            yield b"}"

    clock = [100.0]
    kube.overrides["/apis/apps/v1/deployments"] = lambda _request: httpx.Response(200, stream=Dribble())
    with (
        patch("nvke.infrastructure_client.time.monotonic", side_effect=lambda: clock[0]),
        pytest.raises(NvkeError) as error,
    ):
        kube.inspect()
    assert error.value.code == "OBSERVATION_TIMEOUT" and len(kube.requests) == 1


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--provider-credentials-secret-name", "../PRIVATE"),
        ("--provider-credentials-secret-name", "a/b"),
        ("--provider-credentials-secret-name", "encoded%2fprivate"),
        ("--provider-credentials-namespace", "other/namespace"),
        ("--provider-credentials-namespace", "UPPERCASE"),
    ],
)
def test_untrusted_manager_flags_cannot_retarget_or_inject_secret_paths(kube, flag, value):
    kube.deployments[0] = kube.manager(args=[flag, value])
    result = kube.inspect().providers[0]
    assert result.state == "UNKNOWN" and result.reason == "provider_secret_reference_invalid"
    assert not any("/secrets/" in r.url.path for r in kube.requests)
    assert "PRIVATE" not in repr(result)


def test_new_crd_without_conditions_is_not_installed_until_established(kube):
    path = "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/" + providers()[0].crds[0]
    kube.overrides[path] = httpx.Response(
        200,
        json={
            "metadata": {"name": providers()[0].crds[0]},
            "spec": {"versions": [{"served": True}]},
            "status": {"conditions": None},
        },
    )
    assert kube.inspect().providers[0].state == "NOT-INSTALLED"


def test_identity_on_a_later_native_page_is_selected(kube):
    def page(request):
        second = bool(request.url.params.get("continue"))
        return httpx.Response(
            200,
            json={
                "metadata": {"resourceVersion": "17", "continue": "" if second else "next"},
                "items": kube.identities if second else [],
            },
        )

    kube.overrides[f"/apis/{GROUP}/v1alpha1/namespaces/capnico-system/nicoidentities"] = page
    assert kube.inspect().providers[0].credentials.health == "HEALTHY"


def test_row_budget_does_not_return_a_truncated_configured_manager(kube):
    with patch("nvke.infrastructure_client.MAX_ROWS", 0):
        observation = kube.inspect()
    assert not observation.complete and all(p.state == "UNKNOWN" for p in observation.providers)


def test_a_client_cannot_have_its_deadline_overwritten_by_reentrant_inspection(kube):
    nested = []

    def check():
        with pytest.raises(NvkeError) as error:
            client.inspect()
        nested.append(error.value.code)

    with (
        InfrastructureClient(
            Endpoint("datastore", "https://kubernetes.example.test"),
            transport=httpx.MockTransport(kube.handle),
            before_request=check,
        ) as client,
        patch("nvke.infrastructure_client.time.time_ns", return_value=NOW_NS),
    ):
        assert client.inspect().providers[0].state == "CONFIGURED"
    assert nested and set(nested) == {"OBSERVATION_BUSY"}


@pytest.mark.parametrize("change", ["foreign-namespace", "invalid-name", "invalid-namespace"])
def test_wrong_scope_or_malformed_identity_inventory_is_unreadable_not_absence(kube, change):
    metadata = kube.identities[0]["metadata"]
    if change == "foreign-namespace":
        metadata["namespace"] = "another-org"
    elif change == "invalid-name":
        metadata["name"] = "PRIVATE/INVALID"
    else:
        metadata["namespace"] = "INVALID/NAMESPACE"
    status = kube.inspect().providers[0]
    assert not status.complete and status.credentials.health == "UNREADABLE"
    assert "PRIVATE" not in repr(status)


@pytest.mark.parametrize("http_status", [401, 403, 404, 500])
def test_failed_global_kubernetes_inventory_cannot_be_reported_as_absent_providers(kube, http_status):
    kube.overrides["/apis/apps/v1/deployments"] = httpx.Response(
        http_status, json={"message": "PRIVATE_NATIVE_FAILURE"}
    )
    observation = kube.inspect()
    assert not observation.complete
    assert all(p.state == "UNKNOWN" and p.crds_established is None for p in observation.providers)
    assert len(kube.requests) == 1
    assert "PRIVATE" not in repr(observation)
