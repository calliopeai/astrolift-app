from __future__ import annotations

import base64
from copy import deepcopy
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.cdn_cloud import (
    CloudCdnConfig,
    CloudCdnDriver,
    CloudCdnError,
    CloudCdnNotFound,
    ComputeCdnRestClient,
)

PATHS = {
    "backendBuckets": "global/backendBuckets",
    "backendServices": "global/backendServices",
    "urlMaps": "global/urlMaps",
    "targetHttpsProxies": "global/targetHttpsProxies",
    "targetHttpProxies": "global/targetHttpProxies",
    "addresses": "global/addresses",
    "forwardingRules": "global/forwardingRules",
    "sslCertificates": "global/sslCertificates",
}


class FakeCompute:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, dict[str, Any]]] = {key: {} for key in PATHS}
        self.inserted: list[tuple[str, dict[str, Any]]] = []
        self.patched: list[tuple[str, str, dict[str, Any]]] = []
        self.deleted: list[tuple[str, str]] = []
        self.invalidated: list[tuple[str, str, str]] = []
        self.policies: list[tuple[str, str, str, str]] = []
        self.keys_added: list[tuple[str, str, str, str]] = []
        self.keys_deleted: list[tuple[str, str, str]] = []
        self.operation_error: dict[str, Any] | None = None

    def _self_link(self, collection: str, name: str) -> str:
        return f"https://www.googleapis.com/compute/v1/projects/acme-prod/{PATHS[collection]}/{name}"

    def get_resource(self, collection: str, name: str) -> dict[str, Any]:
        try:
            return deepcopy(self.resources[collection][name])
        except KeyError as exc:
            raise CloudCdnNotFound(f"{collection}/{name}") from exc

    def list_resources(self, collection: str) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.resources[collection].values()]

    def insert_resource(self, collection: str, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body["name"])
        if name in self.resources[collection]:
            raise RuntimeError("already exists")
        if collection == "forwardingRules":
            conflict = next(
                (
                    item
                    for item in self.resources[collection].values()
                    if item.get("IPAddress") == body.get("IPAddress") and item.get("portRange") == body.get("portRange")
                ),
                None,
            )
            if conflict is not None:
                raise RuntimeError("IP/port already belongs to another forwarding rule")
        resource = {**deepcopy(body), "selfLink": self._self_link(collection, name)}
        if collection == "addresses":
            resource.update(address="203.0.113.42", status="IN_USE")
        if collection == "sslCertificates" and resource.get("type") == "MANAGED":
            resource["managed"] = {**resource["managed"], "status": "ACTIVE"}
        self.resources[collection][name] = resource
        self.inserted.append((collection, deepcopy(body)))
        return self._operation(f"insert-{collection}-{name}")

    def patch_resource(
        self,
        collection: str,
        name: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        if name not in self.resources[collection]:
            raise CloudCdnNotFound(name)
        self.resources[collection][name].update(deepcopy(body))
        self.patched.append((collection, name, deepcopy(body)))
        return self._operation(f"patch-{collection}-{name}")

    def delete_resource(self, collection: str, name: str) -> dict[str, Any]:
        if name not in self.resources[collection]:
            raise CloudCdnNotFound(name)
        del self.resources[collection][name]
        self.deleted.append((collection, name))
        return self._operation(f"delete-{collection}-{name}")

    def invalidate_cache(self, url_map: str, *, path: str, host: str = "") -> dict[str, Any]:
        self.invalidated.append((url_map, path, host))
        return self._operation(f"invalidate-{len(self.invalidated)}")

    def add_signed_url_key(
        self,
        collection: str,
        name: str,
        *,
        key_name: str,
        key_value: str,
    ) -> dict[str, Any]:
        self.keys_added.append((collection, name, key_name, key_value))
        return self._operation(f"add-key-{key_name}")

    def delete_signed_url_key(
        self,
        collection: str,
        name: str,
        *,
        key_name: str,
    ) -> dict[str, Any]:
        self.keys_deleted.append((collection, name, key_name))
        return self._operation(f"delete-key-{key_name}")

    def set_backend_policy(
        self,
        collection: str,
        name: str,
        *,
        action: str,
        policy: str,
    ) -> dict[str, Any]:
        field = "edgeSecurityPolicy" if action == "setEdgeSecurityPolicy" else "securityPolicy"
        self.resources[collection][name][field] = policy
        self.policies.append((collection, name, action, policy))
        return self._operation(f"policy-{action}-{name}")

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._operation(name)

    def _operation(self, name: str) -> dict[str, Any]:
        operation = {"name": name, "status": "DONE"}
        if self.operation_error:
            operation["error"] = deepcopy(self.operation_error)
        return operation


@pytest.fixture
def fake() -> FakeCompute:
    return FakeCompute()


@pytest.fixture
def driver(fake: FakeCompute) -> CloudCdnDriver:
    return CloudCdnDriver(
        config=CloudCdnConfig(
            project_id="acme-prod",
            poll_interval_seconds=0,
        ),
        client=fake,
    )


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="portal",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="site",
        size="small",
        config={
            "cdn_id": "portal-cdn",
            "origin_bucket": "acme-portal-assets",
            "domains": ["portal.example.com"],
            **config,
        },
        managed_service_id="service-id",
    )


def marker(*, adopted: bool = False) -> str:
    value = "Astrolift managed CDN; stack=portal-cdn; boundary=acme/portal/prod/cluster-id; resource=service-id"
    return f"{value}; adopted=true" if adopted else value


def test_provision_builds_private_origin_https_stack_with_safe_cache_defaults(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(spec(spa=True, index="index.html"))

    assert result.ok and result.ready
    assert result.handle == "cdn/portal-cdn"
    bucket = fake.resources["backendBuckets"]["portal-cdn-bucket"]
    assert bucket["bucketName"] == "acme-portal-assets"
    assert bucket["enableCdn"] is True
    assert bucket["cdnPolicy"]["cacheMode"] == "CACHE_ALL_STATIC"
    assert bucket["cdnPolicy"]["bypassCacheOnRequestHeaders"] == [
        {"headerName": "Authorization"},
    ]
    url_map = fake.resources["urlMaps"]["portal-cdn-map"]
    policy = url_map["defaultCustomErrorResponsePolicy"]
    assert policy["errorResponseRules"][0] == {
        "matchResponseCodes": ["404"],
        "path": "/index.html",
        "overrideResponseCode": 200,
    }
    certificate = next(iter(fake.resources["sslCertificates"].values()))
    assert certificate["managed"]["domains"] == ["portal.example.com"]
    assert "portal-cdn-https-fr" in fake.resources["forwardingRules"]
    assert "portal-cdn-redirect-fr" in fake.resources["forwardingRules"]


def test_provision_is_idempotent(driver: CloudCdnDriver, fake: FakeCompute) -> None:
    request = spec()
    assert driver.provision(request).ok
    fake.inserted.clear()
    fake.patched.clear()

    result = driver.provision(request)

    assert result.ok
    assert fake.inserted == []
    assert fake.patched == []


def test_cache_and_url_map_updates_reconcile_without_replacing_endpoint(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    fake.inserted.clear()
    result = driver.provision(
        spec(
            cache_policy={
                "cacheMode": "USE_ORIGIN_HEADERS",
                "defaultTtl": 0,
                "maxTtl": 3600,
                "clientTtl": 0,
                "serveWhileStale": 0,
            },
            custom_response_headers=["X-Frame-Options: DENY"],
            url_map={"tests": [{"host": "portal.example.com", "path": "/"}]},
        ),
    )

    assert result.ok
    bucket = fake.resources["backendBuckets"]["portal-cdn-bucket"]
    assert bucket["cdnPolicy"]["cacheMode"] == "USE_ORIGIN_HEADERS"
    assert bucket["customResponseHeaders"] == ["X-Frame-Options: DENY"]
    assert fake.resources["urlMaps"]["portal-cdn-map"]["tests"]
    assert not [call for call in fake.inserted if call[0] == "addresses"]


def test_managed_backend_service_supports_negs_health_checks_and_security(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(
        spec(
            origin_bucket="",
            backends=[
                {
                    "group": (
                        "https://www.googleapis.com/compute/v1/projects/acme-prod/"
                        "regions/us-central1/networkEndpointGroups/api-neg"
                    ),
                    "balancingMode": "RATE",
                    "maxRatePerEndpoint": 100,
                },
            ],
            health_checks=[
                "https://www.googleapis.com/compute/v1/projects/acme-prod/global/healthChecks/api",
            ],
            security_policy="projects/acme-prod/global/securityPolicies/app",
            edge_security_policy="projects/acme-prod/global/securityPolicies/edge",
        ),
    )

    assert result.ok
    service = fake.resources["backendServices"]["portal-cdn-service"]
    assert service["enableCDN"] is True
    assert service["loadBalancingScheme"] == "EXTERNAL_MANAGED"
    assert service["backends"][0]["maxRatePerEndpoint"] == 100
    assert {call[2] for call in fake.policies} == {
        "setSecurityPolicy",
        "setEdgeSecurityPolicy",
    }


def test_external_backend_service_is_referenced_but_never_claimed_or_pruned(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    fake.resources["backendServices"]["shared-api"] = {
        "name": "shared-api",
        "selfLink": fake._self_link("backendServices", "shared-api"),
        "enableCDN": True,
        "description": "owned elsewhere",
    }
    result = driver.provision(
        spec(
            origin_bucket="",
            origin_backend_service="global/backendServices/shared-api",
        ),
    )

    assert result.ok
    assert fake.resources["backendServices"]["shared-api"]["description"] == "owned elsewhere"
    assert not [call for call in fake.patched if call[:2] == ("backendServices", "shared-api")]
    assert driver.deprovision(
        DeprovisionSpec("cdn/portal-cdn", {"deletion_protection": False}),
        force_destroy=True,
    ).ok
    assert "shared-api" in fake.resources["backendServices"]


def test_external_backend_must_already_enable_cdn(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    fake.resources["backendServices"]["shared-api"] = {
        "name": "shared-api",
        "enableCDN": False,
    }
    result = driver.provision(
        spec(
            origin_bucket="",
            origin_backend_service="shared-api",
        ),
    )
    assert not result.ok and "enableCDN=true" in result.message


def test_explicit_insecure_endpoint_uses_http_only_and_prunes_tls_graph(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    result = driver.provision(
        spec(
            domains=[],
            allow_insecure_http=True,
        ),
    )

    assert result.ok and result.ready
    assert set(fake.resources["forwardingRules"]) == {"portal-cdn-http-fr"}
    assert set(fake.resources["targetHttpProxies"]) == {"portal-cdn-http-proxy"}
    assert fake.resources["targetHttpsProxies"] == {}
    assert fake.resources["sslCertificates"] == {}


def test_http_mode_can_switch_back_to_https_redirect_without_ip_port_conflict(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec(domains=[], allow_insecure_http=True)).ok
    result = driver.provision(spec())

    assert result.ok
    assert set(fake.resources["forwardingRules"]) == {
        "portal-cdn-https-fr",
        "portal-cdn-redirect-fr",
    }
    assert ("forwardingRules", "portal-cdn-http-fr") in fake.deleted


def test_https_redirect_can_switch_to_http_without_ip_port_conflict(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    result = driver.provision(spec(domains=[], allow_insecure_http=True))

    assert result.ok
    assert set(fake.resources["forwardingRules"]) == {"portal-cdn-http-fr"}
    assert ("forwardingRules", "portal-cdn-redirect-fr") in fake.deleted


def test_managed_certificate_domain_change_rolls_proxy_then_prunes_old_revision(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    old_cert = next(iter(fake.resources["sslCertificates"]))

    result = driver.provision(spec(domains=["new.example.com"]))

    assert result.ok
    certs = set(fake.resources["sslCertificates"])
    assert old_cert not in certs and len(certs) == 1
    proxy_certs = fake.resources["targetHttpsProxies"]["portal-cdn-https-proxy"]["sslCertificates"]
    assert next(iter(certs)) in proxy_certs[0]


def test_disabling_https_redirect_prunes_only_redirect_graph(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    assert driver.provision(spec(redirect_http_to_https=False)).ok
    assert set(fake.resources["forwardingRules"]) == {"portal-cdn-https-fr"}
    assert fake.resources["targetHttpProxies"] == {}
    assert set(fake.resources["urlMaps"]) == {"portal-cdn-map"}


def test_unowned_mutable_resource_requires_adoption_and_is_marked(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    fake.resources["backendBuckets"]["portal-cdn-bucket"] = {
        "name": "portal-cdn-bucket",
        "description": "legacy",
        "bucketName": "acme-portal-assets",
        "enableCdn": True,
    }
    denied = driver.provision(spec())
    adopted = driver.provision(spec(adopt_existing=True))

    assert not denied.ok and "not owned" in denied.message
    assert adopted.ok
    assert fake.resources["backendBuckets"]["portal-cdn-bucket"]["description"] == marker(
        adopted=True,
    )


def test_compatible_immutable_address_can_be_reused_but_is_not_claimed(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    fake.resources["addresses"]["portal-cdn-ip"] = {
        "name": "portal-cdn-ip",
        "description": "network-team",
        "addressType": "EXTERNAL",
        "ipVersion": "IPV4",
        "networkTier": "PREMIUM",
        "address": "203.0.113.90",
        "selfLink": fake._self_link("addresses", "portal-cdn-ip"),
    }
    result = driver.provision(spec(adopt_existing=True))
    assert result.ok
    assert fake.resources["addresses"]["portal-cdn-ip"]["description"] == "network-team"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"origin_bucket": "", "domains": ["portal.example.com"]}, "exactly one"),
        ({"cdn_id": "Bad_ID"}, "RFC1035"),
        ({"size": "planetary"}, "size must be"),
        ({"compression_mode": "BROTLI"}, "compression_mode"),
        ({"quic_override": "FAST"}, "quic_override"),
        ({"protocol": "TCP"}, "protocol"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"deletion_protection": "yes"}, "must be a boolean"),
        ({"domains": "portal.example.com"}, "list of non-empty"),
        ({"hostname": "not a hostname"}, "valid DNS hostname"),
        ({"cache_policy": {"cacheMode": "INVALID_CACHE_MODE"}}, "cacheMode"),
        (
            {"cache_policy": {"cacheMode": "FORCE_CACHE_ALL"}},
            "allow_force_cache_all",
        ),
        (
            {"cache_policy": {"defaultTtl": 100, "maxTtl": 10}},
            "cannot exceed",
        ),
        (
            {
                "cache_policy": {
                    "cacheKeyPolicy": {
                        "queryStringWhitelist": ["a"],
                        "queryStringBlacklist": ["b"],
                    },
                },
            },
            "mutually exclusive",
        ),
        ({"domains": ["not a domain"]}, "invalid Cloud CDN domain"),
        (
            {"certificate_map": "maps/a", "ssl_certificates": ["certs/a"]},
            "mutually exclusive",
        ),
        ({"domains": [], "ssl_certificates": []}, "HTTPS Cloud CDN requires"),
        (
            {"allow_insecure_http": True},
            "cannot be combined",
        ),
        ({"origin_bucket": "", "backends": [{}]}, "containing group"),
        ({"origin_bucket": "", "backends": [{"group": "neg"}], "spa": True}, "origin_bucket"),
        ({"backend_bucket": {"enableCdn": False}}, "cannot override"),
        ({"forwarding_rule": {"IPAddress": "1.2.3.4"}}, "cannot override"),
        (
            {
                "origin_bucket": "",
                "origin_backend_service": "shared",
                "cache_policy": {"cacheMode": "CACHE_ALL_STATIC"},
            },
            "must be preconfigured",
        ),
    ],
)
def test_validation_rejects_unsafe_or_ambiguous_shapes(
    driver: CloudCdnDriver,
    config: dict[str, Any],
    message: str,
) -> None:
    request = spec(**config)
    result = driver.provision(request)
    assert not result.ok
    assert message in result.message


def test_binding_emits_portable_contract_and_project_role(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    binding = driver.binding(
        ServiceHandle("cdn/portal-cdn"),
        spec().config,
    )
    assert binding.env_vars["CDN_URL"].literal == "https://portal.example.com"
    assert binding.env_vars["CDN_IP_ADDRESS"].literal == "203.0.113.42"
    assert binding.env_vars["GCP_CLOUD_CDN_BACKEND_KIND"].literal == "backend_bucket"
    assert binding.iam_grants[0].actions == ["roles/compute.loadBalancerAdmin"]


def test_portable_size_hint_is_accepted(driver: CloudCdnDriver) -> None:
    assert driver.provision(spec(size="small")).ok


def test_status_discovers_backend_from_url_map_without_config(
    driver: CloudCdnDriver,
) -> None:
    assert driver.provision(spec()).ok
    status = driver.status(ServiceHandle("cdn/portal-cdn"))
    assert status.state == "available"


def test_status_missing_is_deprovisioned(driver: CloudCdnDriver) -> None:
    status = driver.status(ServiceHandle("cdn/missing"))
    assert status.state == "deprovisioned"


def test_invalidation_requires_owned_map_and_valid_paths(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    result = driver.invalidate("portal-cdn", ["/assets/*"], host="portal.example.com")
    assert result["invalidation_id"] == "invalidate-1"
    assert fake.invalidated == [
        ("portal-cdn-map", "/assets/*", "portal.example.com"),
    ]
    with pytest.raises(CloudCdnError, match="start with"):
        driver.invalidate("portal-cdn", ["assets/*"])


def test_signed_url_rotation_is_ephemeral_and_removes_previous_key(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    key = base64.urlsafe_b64encode(b"0123456789abcdef").decode().rstrip("=")
    driver.rotate_signed_url_key(
        ServiceHandle("cdn/portal-cdn"),
        key_name="key-v2",
        key_value_b64=key,
        config=spec().config,
        previous_key_name="key-v1",
    )
    assert fake.keys_added[0][2:] == ("key-v2", key)
    assert fake.keys_deleted[0][2] == "key-v1"


def test_signed_url_rotation_rejects_bad_secret_and_external_without_opt_in(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    with pytest.raises(CloudCdnError, match="16 bytes"):
        driver.rotate_signed_url_key(
            ServiceHandle("cdn/portal-cdn"),
            key_name="bad",
            key_value_b64=base64.urlsafe_b64encode(b"short").decode(),
            config=spec().config,
        )
    with pytest.raises(CloudCdnError, match="URL-safe base64"):
        driver.rotate_signed_url_key(
            ServiceHandle("cdn/portal-cdn"),
            key_name="bad",
            key_value_b64="!!!!!!!!!!!!!!!!!!!!!!",
            config=spec().config,
        )

    fake.resources["backendServices"]["shared"] = {
        "name": "shared",
        "enableCDN": True,
    }
    key = base64.urlsafe_b64encode(b"0123456789abcdef").decode()
    with pytest.raises(CloudCdnError, match="allow_external_key_rotation"):
        driver.rotate_signed_url_key(
            ServiceHandle("cdn/portal-cdn"),
            key_name="key",
            key_value_b64=key,
            config={"origin_backend_service": "shared"},
        )


def test_deprovision_enforces_protection_and_preflights_adopted_resources(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    protected = driver.deprovision(DeprovisionSpec("cdn/portal-cdn", {}))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert fake.deleted == []

    fake.resources["urlMaps"]["portal-cdn-map"]["description"] = marker(adopted=True)
    blocked = driver.deprovision(
        DeprovisionSpec("cdn/portal-cdn", {"deletion_protection": False}),
    )
    assert not blocked.ok and "delete_adopted_resources=true" in blocked.message
    assert fake.deleted == []

    deleted = driver.deprovision(
        DeprovisionSpec(
            "cdn/portal-cdn",
            {"deletion_protection": False, "delete_adopted_resources": True},
        ),
    )
    assert deleted.ok
    assert fake.deleted[0][0] == "forwardingRules"
    assert fake.resources["backendBuckets"] == {}


def test_deprovision_blocks_external_backend_dependents_without_force(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(spec()).ok
    fake.resources["backendBuckets"]["portal-cdn-bucket"]["usedBy"] = [
        {"reference": "global/urlMaps/another-map"},
    ]
    blocked = driver.deprovision(
        DeprovisionSpec("cdn/portal-cdn", {"deletion_protection": False}),
    )
    assert not blocked.ok and "external dependents" in blocked.message
    assert fake.deleted == []


def test_update_requires_existing_owned_url_map(
    driver: CloudCdnDriver,
) -> None:
    result = driver.update(UpdateSpec("cdn/portal-cdn", config=spec().config))
    assert not result.ok and result.errors == ["not_found"]


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}" if payload is not None else b""
        self.text = "provider error"

    def json(self) -> dict[str, Any]:
        return deepcopy(self._payload)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, deepcopy(kwargs)))
        return self.responses.pop(0)


def test_rest_client_uses_compute_global_paths_and_escapes_names() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "cdn-map"}),
            FakeResponse(200, {"name": "op-1", "status": "PENDING"}),
        ],
    )
    client = ComputeCdnRestClient(project_id="acme-prod", session=session)
    client.get_resource("urlMaps", "cdn map")
    client.invalidate_cache("cdn-map", path="/*", host="example.com")

    assert session.calls[0][1] == (
        "https://compute.googleapis.com/compute/v1/projects/acme-prod/global/urlMaps/cdn%20map"
    )
    assert session.calls[1][1].endswith("/global/urlMaps/cdn-map/invalidateCache")
    assert session.calls[1][2]["json"] == {"path": "/*", "host": "example.com"}


def test_rest_client_paginates_and_maps_not_found() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"items": [{"name": "one"}], "nextPageToken": "next"}),
            FakeResponse(200, {"items": [{"name": "two"}]}),
            FakeResponse(404, {"error": {"message": "gone"}}),
        ],
    )
    client = ComputeCdnRestClient(project_id="acme-prod", session=session)
    assert [item["name"] for item in client.list_resources("backendBuckets")] == [
        "one",
        "two",
    ]
    with pytest.raises(CloudCdnNotFound):
        client.get_resource("backendBuckets", "gone")
    assert session.calls[1][2]["params"]["pageToken"] == "next"


def test_provider_operation_error_surfaces_without_partial_success(
    driver: CloudCdnDriver,
    fake: FakeCompute,
) -> None:
    fake.operation_error = {"errors": [{"code": "INVALID_FIELD", "message": "bad"}]}
    result = driver.provision(spec())
    assert not result.ok and "INVALID_FIELD" in result.message
