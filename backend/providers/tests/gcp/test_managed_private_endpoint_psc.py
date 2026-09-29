from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed.private_endpoint_psc import (
    ComputePscRestClient,
    PrivateServiceConnectConfig,
    PrivateServiceConnectDriver,
    PrivateServiceConnectError,
    PrivateServiceConnectNotFound,
)


class FakeCompute:
    def __init__(self) -> None:
        self.addresses: dict[tuple[str, str], dict[str, Any]] = {}
        self.forwarding: dict[tuple[str, str], dict[str, Any]] = {}
        self.inserted: list[tuple[str, str, dict[str, Any]]] = []
        self.patched: list[tuple[str, str, dict[str, Any]]] = []
        self.deleted: list[tuple[str, str, str]] = []
        self.label_updates: list[tuple[str, str, str, dict[str, str]]] = []
        self.operation_error: dict[str, Any] | None = None

    @staticmethod
    def _scope(location: str) -> str:
        return "global" if location == "global" else f"regions/{location}"

    def _link(self, location: str, collection: str, name: str) -> str:
        return f"https://www.googleapis.com/compute/v1/projects/acme-prod/{self._scope(location)}/{collection}/{name}"

    def get_address(self, location: str, name: str) -> dict[str, Any]:
        try:
            return deepcopy(self.addresses[(location, name)])
        except KeyError as exc:
            raise PrivateServiceConnectNotFound(f"address/{location}/{name}") from exc

    def insert_address(self, location: str, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body["name"])
        key = (location, name)
        if key in self.addresses:
            raise RuntimeError("address exists")
        resource = {
            **deepcopy(body),
            "address": body.get("address") or ("fd20::10" if body.get("ipVersion") == "IPV6" else "10.42.0.10"),
            "selfLink": self._link(location, "addresses", name),
            "labelFingerprint": "fp-address-1",
            "users": [],
        }
        self.addresses[key] = resource
        self.inserted.append(("address", location, deepcopy(body)))
        return self._operation(f"insert-address-{name}")

    def delete_address(self, location: str, name: str) -> dict[str, Any]:
        try:
            del self.addresses[(location, name)]
        except KeyError as exc:
            raise PrivateServiceConnectNotFound(name) from exc
        self.deleted.append(("address", location, name))
        return self._operation(f"delete-address-{name}")

    def set_address_labels(
        self,
        location: str,
        name: str,
        *,
        labels: dict[str, str],
        fingerprint: str,
    ) -> dict[str, Any]:
        assert fingerprint == self.addresses[(location, name)].get("labelFingerprint", "")
        self.addresses[(location, name)]["labels"] = deepcopy(labels)
        self.addresses[(location, name)]["labelFingerprint"] = "fp-address-2"
        self.label_updates.append(("address", location, name, deepcopy(labels)))
        return self._operation(f"labels-address-{name}")

    def get_forwarding_rule(self, location: str, name: str) -> dict[str, Any]:
        try:
            return deepcopy(self.forwarding[(location, name)])
        except KeyError as exc:
            raise PrivateServiceConnectNotFound(f"forwarding/{location}/{name}") from exc

    def insert_forwarding_rule(
        self,
        location: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(body["name"])
        key = (location, name)
        if key in self.forwarding:
            raise RuntimeError("forwarding rule exists")
        google_apis = str(body.get("target")) in {"all-apis", "vpc-sc"}
        resource = {
            **deepcopy(body),
            "selfLink": self._link(location, "forwardingRules", name),
            "selfLinkWithId": f"{self._link(location, 'forwardingRules', name)}/123456789",
            "labelFingerprint": "fp-forwarding-1",
            "status": "ACTIVE",
        }
        if not google_apis:
            resource["pscConnectionStatus"] = "ACCEPTED"
        self.forwarding[key] = resource
        for address in self.addresses.values():
            if address.get("address") == body.get("IPAddress"):
                address.setdefault("users", []).append(resource["selfLink"])
        self.inserted.append(("forwarding", location, deepcopy(body)))
        return self._operation(f"insert-forwarding-{name}")

    def patch_forwarding_rule(
        self,
        location: str,
        name: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.forwarding[(location, name)].update(deepcopy(body))
        self.patched.append((location, name, deepcopy(body)))
        return self._operation(f"patch-forwarding-{name}")

    def delete_forwarding_rule(self, location: str, name: str) -> dict[str, Any]:
        try:
            resource = self.forwarding.pop((location, name))
        except KeyError as exc:
            raise PrivateServiceConnectNotFound(name) from exc
        for address in self.addresses.values():
            address["users"] = [user for user in address.get("users") or [] if user != resource.get("selfLink")]
        self.deleted.append(("forwarding", location, name))
        return self._operation(f"delete-forwarding-{name}")

    def set_forwarding_rule_labels(
        self,
        location: str,
        name: str,
        *,
        labels: dict[str, str],
        fingerprint: str,
    ) -> dict[str, Any]:
        assert fingerprint == self.forwarding[(location, name)].get("labelFingerprint", "")
        self.forwarding[(location, name)]["labels"] = deepcopy(labels)
        self.forwarding[(location, name)]["labelFingerprint"] = "fp-forwarding-2"
        self.label_updates.append(("forwarding", location, name, deepcopy(labels)))
        return self._operation(f"labels-forwarding-{name}")

    def get_operation(self, location: str, name: str) -> dict[str, Any]:
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
def driver(fake: FakeCompute) -> PrivateServiceConnectDriver:
    return PrivateServiceConnectDriver(
        config=PrivateServiceConnectConfig(
            project_id="acme-prod",
            region="us-central1",
            network="platform",
            subnetwork="apps",
            poll_interval_seconds=0,
        ),
        client=fake,
    )


MSID = "service-id"


def service_spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="portal",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="payments",
        size="small",
        config={
            "endpoint_id": "payments-psc",
            "endpoint_type": "service_attachment",
            "service_attachment": ("projects/payments-prod/regions/us-central1/serviceAttachments/payments"),
            **config,
        },
        managed_service_id=MSID,
    )


def api_spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="portal",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="googleapis",
        size="small",
        config={
            "endpoint_id": "googleapis1",
            "endpoint_type": "google_apis",
            "api_bundle": "vpc-sc",
            "ip_address": "10.100.0.10",
            **config,
        },
        managed_service_id=MSID,
    )


def ownership(endpoint: str, *, adopted: bool = False) -> dict[str, str]:
    labels = {
        "astrolift-managed-by": "platform",
        "astrolift-private-endpoint": endpoint,
    }
    if adopted:
        labels["astrolift-adopted"] = "true"
    return labels


def test_provision_regional_service_attachment_endpoint(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(
        service_spec(
            allow_global_access=True,
            service_directory={"namespace": "payments", "service": "api"},
            labels={"team": "payments"},
        ),
    )

    assert result.ok and result.ready
    assert result.handle == "private_endpoint/us-central1/payments-psc"
    address = fake.addresses[("us-central1", "payments-psc-ip")]
    assert address["addressType"] == "INTERNAL"
    assert address["subnetwork"].endswith("/regions/us-central1/subnetworks/apps")
    endpoint = fake.forwarding[("us-central1", "payments-psc")]
    assert endpoint["target"].endswith(
        "/projects/payments-prod/regions/us-central1/serviceAttachments/payments",
    )
    assert endpoint["network"].endswith("/global/networks/platform")
    assert endpoint["allowPscGlobalAccess"] is True
    assert endpoint["serviceDirectoryRegistrations"] == [
        {"namespace": "payments", "service": "api"},
    ]
    assert endpoint["labels"]["team"] == "payments"


def test_provision_global_google_apis_endpoint(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(
        api_spec(
            service_directory={"namespace": "googleapis", "region": "us-east1"},
        ),
    )

    assert result.ok and result.ready
    assert result.handle == "private_endpoint/global/googleapis1"
    address = fake.addresses[("global", "googleapis1-ip")]
    assert address["purpose"] == "PRIVATE_SERVICE_CONNECT"
    assert address["network"].endswith("/global/networks/platform")
    endpoint = fake.forwarding[("global", "googleapis1")]
    assert endpoint["target"] == "vpc-sc"
    assert endpoint["loadBalancingScheme"] == ""
    assert endpoint["serviceDirectoryRegistrations"] == [
        {"namespace": "googleapis", "serviceDirectoryRegion": "us-east1"},
    ]


def test_provision_is_idempotent(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    request = service_spec()
    assert driver.provision(request).ok
    fake.inserted.clear()
    fake.patched.clear()

    assert driver.provision(request).ok
    assert fake.inserted == []
    assert fake.patched == []


def test_global_access_is_the_only_in_place_provider_update(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    assert driver.provision(service_spec()).ok
    result = driver.update(
        UpdateSpec(
            "private_endpoint/us-central1/payments-psc",
            managed_service_id=MSID,
            config=service_spec(allow_global_access=True).config,
        ),
    )

    assert result.ok
    assert fake.patched == [
        ("us-central1", "payments-psc", {"allowPscGlobalAccess": True}),
    ]


def test_immutable_target_change_requires_reprovision(
    driver: PrivateServiceConnectDriver,
) -> None:
    assert driver.provision(service_spec()).ok
    result = driver.update(
        UpdateSpec(
            "private_endpoint/us-central1/payments-psc",
            managed_service_id=MSID,
            config=service_spec(
                service_attachment=("projects/payments-prod/regions/us-central1/serviceAttachments/payments-v2"),
            ).config,
        ),
    )
    assert not result.ok and "immutable" in result.message


def test_explicit_external_address_is_validated_and_retained(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    fake.addresses[("us-central1", "shared-psc-ip")] = {
        "name": "shared-psc-ip",
        "address": "10.42.0.99",
        "addressType": "INTERNAL",
        "ipVersion": "IPV4",
        "subnetwork": ("https://www.googleapis.com/compute/v1/projects/acme-prod/regions/us-central1/subnetworks/apps"),
        "description": "network team",
        "labels": {"owner": "network"},
        "labelFingerprint": "fp-address-1",
        "selfLink": fake._link("us-central1", "addresses", "shared-psc-ip"),
        "users": [],
    }
    request = service_spec(address_resource="shared-psc-ip")
    result = driver.provision(request)
    assert result.ok
    assert not [row for row in fake.label_updates if row[0] == "address"]

    deleted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            managed_service_id=MSID,
            config={**request.config, "deletion_protection": False},
        ),
    )
    assert deleted.ok
    assert ("us-central1", "shared-psc-ip") in fake.addresses


def test_unowned_address_and_endpoint_are_refused_without_operator_adoption(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    request = service_spec()
    assert driver.provision(request).ok
    address = fake.addresses[("us-central1", "payments-psc-ip")]
    endpoint = fake.forwarding[("us-central1", "payments-psc")]
    owned_address_labels = dict(address["labels"])
    address["labels"] = {"owner": "network"}
    endpoint["labels"] = {"owner": "network"}
    updates = len(fake.label_updates)

    denied = driver.provision(request)
    assert not denied.ok
    assert "operator-authorized" in denied.message

    # The flag is gone entirely, not just ignored: adoption is operator-only
    # and no tenant config reopens it (#2021).
    rejected = driver.provision(service_spec(adopt_existing=True))
    assert not rejected.ok
    assert "unknown" in rejected.message

    address["labels"] = owned_address_labels
    endpoint_denied = driver.provision(request)
    assert not endpoint_denied.ok
    assert "forwarding rule" in endpoint_denied.message
    assert "operator-authorized" in endpoint_denied.message
    assert endpoint["labels"] == {"owner": "network"}
    assert len(fake.label_updates) == updates


def test_resources_adopted_before_2074_stay_protected_on_delete(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    request = service_spec()
    result = driver.provision(request)
    assert result.ok
    fake.addresses[("us-central1", "payments-psc-ip")]["labels"] = ownership("payments-psc", adopted=True)
    fake.forwarding[("us-central1", "payments-psc")]["labels"] = ownership("payments-psc", adopted=True)

    blocked = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            managed_service_id=MSID,
            config={**request.config, "deletion_protection": False},
        ),
    )
    assert not blocked.ok and "delete_adopted_resources" in blocked.message
    accepted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            managed_service_id=MSID,
            config={
                **request.config,
                "deletion_protection": False,
                "delete_adopted_resources": True,
            },
        ),
    )
    assert accepted.ok


def test_status_maps_provider_connection_states(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(service_spec())
    endpoint = fake.forwarding[("us-central1", "payments-psc")]
    endpoint["pscConnectionStatus"] = "PENDING"
    assert driver.status(ServiceHandle(result.handle, managed_service_id=MSID)).state == "provisioning"
    endpoint["pscConnectionStatus"] = "REJECTED"
    assert driver.status(ServiceHandle(result.handle, managed_service_id=MSID)).state == "error"
    endpoint["pscConnectionStatus"] = "ACCEPTED"
    assert driver.status(ServiceHandle(result.handle, managed_service_id=MSID)).state == "available"


def test_status_missing_is_deprovisioned(driver: PrivateServiceConnectDriver) -> None:
    status = driver.status(ServiceHandle("private_endpoint/us-central1/missing"))
    assert status.state == "deprovisioned"


def test_binding_emits_portable_and_gcp_contract(
    driver: PrivateServiceConnectDriver,
) -> None:
    request = service_spec(
        dns_name="payments.internal.example.com",
        url_scheme="https",
        port=8443,
    )
    result = driver.provision(request)
    binding = driver.binding(ServiceHandle(result.handle, managed_service_id=MSID), request.config)

    assert binding.env_vars["PRIVATE_ENDPOINT_URL"].literal == ("https://payments.internal.example.com:8443")
    assert binding.env_vars["PRIVATE_ENDPOINT_IPS"].literal == '["10.42.0.10"]'
    assert binding.env_vars["PRIVATE_ENDPOINT_TYPE"].literal == "service_attachment"
    assert binding.env_vars["GCP_PSC_REGION"].literal == "us-central1"
    assert binding.env_vars["GCP_PSC_CONNECTION_STATUS"].literal == "ACCEPTED"
    assert "/123456789" in str(binding.env_vars["GCP_PSC_ENDPOINT_URI"].literal)


def test_deprovision_enforces_protection_and_deletes_rule_before_address(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(service_spec())
    protected = driver.deprovision(DeprovisionSpec(result.handle, service_spec().config, managed_service_id=MSID))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert fake.deleted == []

    deleted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            managed_service_id=MSID,
            config={**service_spec().config, "deletion_protection": False},
        ),
    )
    assert deleted.ok
    assert fake.deleted == [
        ("forwarding", "us-central1", "payments-psc"),
        ("address", "us-central1", "payments-psc-ip"),
    ]


def test_deprovision_blocks_address_with_external_dependents(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(service_spec())
    fake.addresses[("us-central1", "payments-psc-ip")]["users"].append(
        "regions/us-central1/forwardingRules/someone-else",
    )
    blocked = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            managed_service_id=MSID,
            config={**service_spec().config, "deletion_protection": False},
        ),
    )
    assert not blocked.ok and blocked.errors == ["address_in_use"]
    assert ("us-central1", "payments-psc-ip") in fake.addresses
    assert ("us-central1", "payments-psc") in fake.forwarding


def test_deprovision_missing_stack_is_idempotent(
    driver: PrivateServiceConnectDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(
            "private_endpoint/us-central1/missing",
            managed_service_id=MSID,
            config={
                **service_spec(endpoint_id="missing").config,
                "deletion_protection": False,
            },
        ),
    )
    assert result.ok


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"network": ""}, "network is required"),
        ({"service_attachment": ""}, "service_attachment is required"),
        (
            {
                "service_attachment": ("projects/p/regions/us-east1/serviceAttachments/service"),
            },
            "region must match",
        ),
        ({"endpoint_id": "Bad_ID"}, "RFC1035"),
        ({"address_id": "Bad_ID"}, "RFC1035"),
        ({"ip_version": "IPV9"}, "ip_version"),
        ({"ip_address": "8.8.8.8"}, "must be private"),
        ({"ip_address": "fd20::1", "ip_version": "IPV4"}, "does not match"),
        ({"allow_global_access": "yes"}, "must be a boolean"),
        ({"dns_name": "not a hostname"}, "valid hostname"),
        ({"url_scheme": "HTTPS"}, "lowercase URI scheme"),
        ({"port": True}, "port must be"),
        ({"labels": {"Bad Key": "value"}}, "invalid GCP label key"),
        (
            {"labels": {"astrolift-managed-by": "someone"}},
            "ownership labels",
        ),
        ({"service_directory": {}}, "must contain"),
        ({"address": {"purpose": "SHARED_LOADBALANCER_VIP"}}, "cannot override"),
        ({"forwarding_rule": {"target": "all-apis"}}, "cannot override"),
        ({"unknown": True}, "unknown Private Service Connect"),
    ],
)
def test_service_endpoint_validation_rejects_unsafe_shapes(
    driver: PrivateServiceConnectDriver,
    config: dict[str, Any],
    message: str,
) -> None:
    if config.get("network") == "":
        driver = PrivateServiceConnectDriver(
            config=PrivateServiceConnectConfig(
                project_id="acme-prod",
                region="us-central1",
                network="",
                subnetwork="apps",
            ),
            client=FakeCompute(),
        )
    result = driver.provision(service_spec(**config))
    assert not result.ok
    assert message in result.message


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"ip_address": ""}, "require ip_address"),
        ({"endpoint_id": "google-api"}, "1-20 lowercase"),
        ({"api_bundle": "everything"}, "api_bundle"),
        ({"service_attachment": "projects/p/regions/r/serviceAttachments/x"}, "not valid"),
        ({"subnetwork": "apps"}, "not valid"),
        ({"allow_global_access": True}, "only valid"),
        ({"ip_version": "IPV6", "ip_address": "fd20::1"}, "require IPV4"),
    ],
)
def test_google_api_endpoint_validation_rejects_unsafe_shapes(
    driver: PrivateServiceConnectDriver,
    config: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(api_spec(**config))
    assert not result.ok
    assert message in result.message


def test_update_rejects_handle_location_or_id_drift(
    driver: PrivateServiceConnectDriver,
) -> None:
    assert driver.provision(service_spec()).ok
    moved = driver.update(
        UpdateSpec(
            "private_endpoint/us-central1/payments-psc",
            managed_service_id=MSID,
            config=service_spec(region="us-east1").config,
        ),
    )
    assert not moved.ok and "location is immutable" in moved.message


def test_schema_and_editable_fields_are_explicit(
    driver: PrivateServiceConnectDriver,
) -> None:
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert "service_attachment" in schema["properties"]
    assert driver.editable_fields() == [
        "allow_global_access",
        "labels",
        "dns_name",
        "url_scheme",
        "port",
        "deletion_protection",
        "delete_adopted_resources",
    ]


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


def test_rest_client_uses_regional_and_global_compute_paths() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "regional"}),
            FakeResponse(200, {"name": "global"}),
            FakeResponse(200, {"name": "op", "status": "DONE"}),
        ],
    )
    client = ComputePscRestClient(project_id="acme-prod", session=session)
    client.get_forwarding_rule("us-central1", "my endpoint")
    client.get_address("global", "apis")
    client.get_operation("us-central1", "operations/op")

    assert session.calls[0][1].endswith(
        "/projects/acme-prod/regions/us-central1/forwardingRules/my%20endpoint",
    )
    assert session.calls[1][1].endswith("/projects/acme-prod/global/addresses/apis")
    assert session.calls[2][1].endswith(
        "/projects/acme-prod/regions/us-central1/operations/op",
    )


def test_rest_client_maps_not_found_and_sends_label_fingerprint() -> None:
    session = FakeSession(
        [
            FakeResponse(404, {"error": {"message": "gone"}}),
            FakeResponse(200, {"name": "labels", "status": "DONE"}),
        ],
    )
    client = ComputePscRestClient(project_id="acme-prod", session=session)
    with pytest.raises(PrivateServiceConnectNotFound):
        client.get_address("global", "missing")
    client.set_forwarding_rule_labels(
        "global",
        "apis",
        labels={"team": "platform"},
        fingerprint="abc",
    )
    assert session.calls[1][2]["json"] == {
        "labels": {"team": "platform"},
        "labelFingerprint": "abc",
    }


def test_provider_operation_error_is_not_reported_as_success(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    fake.operation_error = {"errors": [{"code": "INVALID_FIELD"}]}
    result = driver.provision(service_spec())
    assert not result.ok and "INVALID_FIELD" in result.message


def test_snapshot_is_explicitly_unsupported(driver: PrivateServiceConnectDriver) -> None:
    with pytest.raises(PrivateServiceConnectError, match="no durable snapshot"):
        driver.snapshot(ServiceHandle("private_endpoint/us-central1/payments-psc"))


# ---- #2086: ownership is the managed-service id, not the tenant-chosen endpoint id ----

VICTIM = "01996b1a-3c4d-7e8f-9a0b-1c2d3e4f5a6b"
STRANGER = "01996b1a-ffff-7e8f-9a0b-aaaaaaaaaaaa"


def _as(spec: ProvisionSpec, managed_service_id: str, **identity: Any) -> ProvisionSpec:
    return replace(spec, managed_service_id=managed_service_id, **identity)


def _derived(spec: ProvisionSpec) -> ProvisionSpec:
    """The same spec with no ``endpoint_id``, so the driver derives one."""
    return replace(spec, config={key: value for key, value in spec.config.items() if key != "endpoint_id"})


def _strip_identity(fake: FakeCompute, endpoint_id: str, *, description: str | None = None) -> None:
    """Make the stack look provisioned before #2086: no managed-service label."""
    for resource in (
        fake.forwarding[("us-central1", endpoint_id)],
        fake.addresses[("us-central1", f"{endpoint_id}-ip")],
    ):
        resource["labels"] = {k: v for k, v in resource["labels"].items() if k != MANAGED_SERVICE_ID_LABEL}
        if description is not None:
            resource["description"] = description


def _identity_labels(fake: FakeCompute, endpoint_id: str) -> list[str]:
    return [
        str(resource["labels"].get(MANAGED_SERVICE_ID_LABEL, ""))
        for resource in (
            fake.forwarding[("us-central1", endpoint_id)],
            fake.addresses[("us-central1", f"{endpoint_id}-ip")],
        )
    ]


def _mutations(fake: FakeCompute) -> tuple[int, int, int, int]:
    return len(fake.inserted), len(fake.patched), len(fake.deleted), len(fake.label_updates)


def test_new_endpoints_carry_the_managed_service_id(driver: PrivateServiceConnectDriver, fake: FakeCompute) -> None:
    assert driver.provision(_as(service_spec(), VICTIM)).ok
    assert _identity_labels(fake, "payments-psc") == [VICTIM, VICTIM]


def test_a_tenant_naming_another_orgs_endpoint_id_is_refused_and_it_is_left_untouched(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    victim = driver.provision(_as(service_spec(), VICTIM))
    assert victim.ok
    endpoint_before = deepcopy(fake.forwarding[("us-central1", "payments-psc")])
    address_before = deepcopy(fake.addresses[("us-central1", "payments-psc-ip")])
    before = _mutations(fake)

    # The attack names the victim's endpoint id and asks for changes that
    # would land on it: new labels and global access.
    attack = _as(
        service_spec(labels={"team": "globex"}, allow_global_access=True), STRANGER, organization_slug="globex"
    )
    denied = driver.provision(attack)
    assert not denied.ok and "another managed service" in denied.message

    stranger: dict[str, Any] = {"managed_service_id": STRANGER, "recorded_handle_exclusive": True}
    updated = driver.update(UpdateSpec(victim.handle, config=attack.config, **stranger))
    assert not updated.ok and "another managed service" in updated.message
    with pytest.raises(PrivateServiceConnectError, match="another managed service"):
        driver.binding(ServiceHandle(victim.handle, **stranger), attack.config)
    assert driver.status(ServiceHandle(victim.handle, **stranger)).state == "error"
    deleted = driver.deprovision(
        DeprovisionSpec(victim.handle, {**attack.config, "deletion_protection": False}, **stranger),
        force_destroy=True,
    )
    assert not deleted.ok and deleted.errors == ["resource_not_owned"] and deleted.retryable is False

    assert _mutations(fake) == before
    assert fake.forwarding[("us-central1", "payments-psc")] == endpoint_before
    assert fake.addresses[("us-central1", "payments-psc-ip")] == address_before


def test_two_orgs_whose_slugs_join_alike_derive_separate_endpoints(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    first = driver.provision(_as(_derived(service_spec()), VICTIM, organization_slug="acme", app_slug="web-prod"))
    second = driver.provision(_as(_derived(service_spec()), STRANGER, organization_slug="acme-web", app_slug="prod"))
    assert first.ok and second.ok
    assert first.handle != second.handle

    apis_first = driver.provision(_as(_derived(api_spec()), VICTIM, organization_slug="acme", app_slug="web-prod"))
    apis_second = driver.provision(
        _as(_derived(api_spec(ip_address="10.100.0.11")), STRANGER, organization_slug="acme-web", app_slug="prod"),
    )
    assert apis_first.ok and apis_second.ok
    assert apis_first.handle != apis_second.handle


def test_an_endpoint_from_before_2086_is_proven_by_its_create_time_description(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    request = _as(service_spec(), VICTIM)
    created = driver.provision(request)
    _strip_identity(fake, "payments-psc")

    # The description written at create names the victim, so no record is
    # needed for it, and no record, however exclusive, gets anyone else in.
    taken = driver.update(
        UpdateSpec(created.handle, config=request.config, managed_service_id=STRANGER, recorded_handle_exclusive=True),
    )
    assert not taken.ok and "created for another managed service" in taken.message
    assert _identity_labels(fake, "payments-psc") == ["", ""]

    updated = driver.update(UpdateSpec(created.handle, config=request.config, managed_service_id=VICTIM))
    assert updated.ok
    assert _identity_labels(fake, "payments-psc") == [VICTIM, VICTIM]


def test_an_endpoint_without_create_time_evidence_needs_an_exclusive_record(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    request = _as(service_spec(), VICTIM)
    created = driver.provision(request)
    # Adopted before #2074: the description is whatever its creator wrote.
    _strip_identity(fake, "payments-psc", description="network team")
    before = _mutations(fake)

    assert "exclusive platform record" in driver.provision(request).message
    unproven = driver.update(UpdateSpec(created.handle, config=request.config, managed_service_id=VICTIM))
    assert not unproven.ok and "exclusive platform record" in unproven.message
    with pytest.raises(PrivateServiceConnectError, match="exclusive platform record"):
        driver.binding(ServiceHandle(created.handle, managed_service_id=VICTIM), request.config)
    deleted = driver.deprovision(
        DeprovisionSpec(created.handle, {**request.config, "deletion_protection": False}, managed_service_id=VICTIM),
    )
    assert not deleted.ok and deleted.errors == ["resource_not_owned"]
    assert _mutations(fake) == before

    proven = driver.provision(replace(request, recorded_handle=created.handle, recorded_handle_exclusive=True))
    assert proven.ok
    assert _identity_labels(fake, "payments-psc") == [VICTIM, VICTIM]
    # Backfilled: the label decides from here, whatever anyone's record says.
    taken = driver.update(
        UpdateSpec(created.handle, config=request.config, managed_service_id=STRANGER, recorded_handle_exclusive=True),
    )
    assert not taken.ok and "another managed service" in taken.message


def test_a_service_named_before_2086_keeps_its_recorded_endpoint(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    legacy_id = "astrolift-acme-portal-prod-payments"
    legacy_handle = f"private_endpoint/us-central1/{legacy_id}"
    assert driver.provision(_as(service_spec(endpoint_id=legacy_id), VICTIM)).handle == legacy_handle
    _strip_identity(fake, legacy_id)
    inserted = len(fake.inserted)

    kept = driver.provision(
        replace(_as(_derived(service_spec()), VICTIM), recorded_handle=legacy_handle, recorded_handle_exclusive=True),
    )
    assert kept.ok and kept.handle == legacy_handle
    assert len(fake.inserted) == inserted
    assert _identity_labels(fake, legacy_id) == [VICTIM, VICTIM]

    # Without that record the same spec derives a new-style name.
    fresh = driver.provision(_as(_derived(service_spec()), VICTIM))
    assert fresh.ok and fresh.handle != legacy_handle


@pytest.mark.parametrize(
    "key", [MANAGED_SERVICE_ID_LABEL, "astrolift-managed-service-id", "astrolift-io-managed-service-id"]
)
def test_tenant_labels_cannot_plant_a_managed_service_id(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
    key: str,
) -> None:
    result = driver.provision(_as(service_spec(labels={key: VICTIM}), STRANGER))
    assert not result.ok and "ownership labels" in result.message
    assert not fake.inserted


def test_provision_without_a_managed_service_id_fails_closed(
    driver: PrivateServiceConnectDriver,
    fake: FakeCompute,
) -> None:
    result = driver.provision(_as(service_spec(), ""))
    assert not result.ok and "managed-service id" in result.message
    assert not fake.inserted
