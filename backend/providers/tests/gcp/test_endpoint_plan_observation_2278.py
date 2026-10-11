"""Read-only source facade through real GAPIC serializers and bounded native channel."""

import pytest
from google.auth.credentials import AnonymousCredentials

from gcp.identity_owned import NativeGCPIdentity, NativeIdentityError

from .test_identity_owned_2278 import CONTEXT, ENDPOINT, ROLE, UID, Wire, grants
from .test_identity_owned_2278 import world as owned_world


@pytest.fixture
def world():
    return owned_world.__wrapped__()


def test_prediction_role_facade_reads_native_mapping_and_owner_before_and_after_role():
    wire = Wire()
    with NativeGCPIdentity(CONTEXT, clients=wire.clients) as driver:
        assert driver.verify_prediction_roles((ROLE,), checkpoint=lambda: None) is None
    assert [name for name, _ in wire.calls] == [
        "GetProject",
        "GetServiceAccount",
        "GetRole",
        "GetProject",
        "GetServiceAccount",
    ]
    assert wire.writes == 0


@pytest.mark.parametrize(
    "roles", [("roles/aiplatform.user",), ("projects/foreign-project/roles/Predict",), (ROLE, ROLE), [], (), ({},)]
)
def test_prediction_role_facade_refuses_broad_foreign_and_ambiguous_before_any_native_call(roles):
    wire = Wire()
    with pytest.raises(NativeIdentityError, match="PREDICTION_CUSTOM_ROLE_REQUIRED"):
        NativeGCPIdentity(CONTEXT, clients=wire.clients).verify_prediction_roles(roles, checkpoint=lambda: None)
    assert not wire.calls and wire.writes == 0


@pytest.mark.parametrize("change", ["permissions", "stage", "deleted", "name", "gsa_after_role", "project_after_role"])
def test_prediction_role_native_response_or_final_owner_withdrawal_refuses_without_writes(change):
    wire = Wire()
    if change == "permissions":
        wire.role.included_permissions.append("aiplatform.endpoints.update")
    elif change == "stage":
        wire.role.stage = 1
    elif change == "deleted":
        wire.role.deleted = True
    elif change == "name":
        wire.role.name = ROLE + "Changed"
    else:

        def after(name):
            if name == "GetRole":
                if change == "gsa_after_role":
                    wire.account.unique_id = "110012345678901234568"
                else:
                    wire.project.state = 2

        wire.after_call = after
    with pytest.raises(NativeIdentityError):
        NativeGCPIdentity(CONTEXT, clients=wire.clients).verify_prediction_roles((ROLE,), checkpoint=lambda: None)
    assert wire.writes == 0 and all(name not in {"GetIamPolicy", "SetIamPolicy"} for name, _ in wire.calls)


def test_authority_refuses_before_adc_and_withdrawal_after_adc_prevents_client_construction(monkeypatch):
    import google.auth
    from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

    calls = []
    admitted = False

    def checkpoint():
        if not admitted:
            raise NativeIdentityError("AUTHORITY_WITHDRAWN")

    def adc(**kwargs):
        nonlocal admitted
        calls.append("ADC")
        admitted = False
        return AnonymousCredentials(), None

    def channel(*args, **kwargs):
        calls.append("CHANNEL")
        pytest.fail("withdrawn authority constructed a native transport")

    monkeypatch.setattr(google.auth, "default", adc)
    monkeypatch.setattr(ProjectsGrpcTransport, "create_channel", channel)
    driver = NativeGCPIdentity(CONTEXT)
    with pytest.raises(NativeIdentityError, match="AUTHORITY_WITHDRAWN"):
        driver.verify_prediction_roles((ROLE,), checkpoint=checkpoint)
    assert calls == []
    admitted = True
    with pytest.raises(NativeIdentityError, match="AUTHORITY_WITHDRAWN"):
        driver.verify_prediction_roles((ROLE,), checkpoint=checkpoint)
    assert calls == ["ADC"]


@pytest.mark.parametrize("withdrawal", ["deleted", "denied"])
def test_empty_owned_union_removes_recorded_endpoint_grant_without_role_or_catalogue_reads(world, withdrawal):
    from google.api_core.exceptions import PermissionDenied

    wire, state, reconcile = world
    reconcile()
    assert (ROLE, "serviceAccount:" + CONTEXT.email) in grants(wire.policies[ENDPOINT])
    wire.calls.clear()
    wire.role.deleted = True

    def withdrawn(name):
        if name in {"GetRole", "GetEndpoint"}:
            if withdrawal == "denied":
                raise PermissionDenied("controlled-private-marker")
            pytest.fail("Empty removal queried role or endpoint metadata")

    wire.after_call = withdrawn
    reconcile([])
    assert (ROLE, "serviceAccount:" + CONTEXT.email) not in grants(wire.policies[ENDPOINT])
    assert ("roles/iam.workloadIdentityUser", CONTEXT.principal(UID)) in grants(
        wire.policies[CONTEXT.service_account_resource]
    )
    assert all(name not in {"GetRole", "GetEndpoint"} for name, _ in wire.calls)
    assert not state["ledger"].pending and not state["ledger"].removals
