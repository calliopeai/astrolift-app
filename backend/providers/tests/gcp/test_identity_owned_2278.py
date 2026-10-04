"""Actual v1beta1/IAM/CRM GAPIC serializers over an owned no-network channel."""

from dataclasses import replace
from typing import Any

import grpc
import pytest
from google.api_core.exceptions import Aborted, PermissionDenied, ServiceUnavailable
from google.auth.credentials import AnonymousCredentials
from google.cloud import aiplatform_v1beta1 as vertex
from google.cloud import iam_admin_v1 as iam
from google.cloud import resourcemanager_v3 as manager
from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.grpc import EndpointServiceGrpcTransport
from google.cloud.iam_admin_v1.services.iam.transports.grpc import IAMGrpcTransport
from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport
from google.iam.v1 import iam_policy_pb2, policy_pb2

from _sdk.cloud_credentials import CloudCredential
from gcp.identity_owned import (
    NativeGCPIdentity,
    NativeIdentityContext,
    NativeIdentityError,
    OwnedGrantLedger,
)
from gcp.identity_wi import GCPWIConfig, GCPWorkloadIdentityDriver

PROJECT = "fixture-project"
NUMBER = "415104041262"
REGION = "us-central1"
ROLE = f"projects/{PROJECT}/roles/AstroEndpointPredict"
UID = "12345678-0000-4000-8000-000000000004"
CONTEXT = NativeIdentityContext(
    "12345678-0000-4000-8000-000000000001",
    "12345678-0000-4000-8000-000000000002",
    "12345678-0000-4000-8000-000000000003",
    PROJECT,
    NUMBER,
    REGION,
    "owned-fixture",
    "110012345678901234567",
    CloudCredential("gcp", declared_account=PROJECT),
)
ENDPOINT = f"projects/{NUMBER}/locations/{REGION}/endpoints/73182"
SECOND = ENDPOINT[:-5] + "73183"
PERMISSIONS = [{"role": ROLE, "resource": ENDPOINT}]


class Wire(grpc.Channel):
    def __init__(self):
        self.calls: list[tuple[str, Any]] = []
        self.writes = 0
        self.closed = 0
        self.error = None
        self.lost_reply = False
        self.after_call = None
        self.project = manager.Project(name=f"projects/{NUMBER}", project_id=PROJECT, state=1)
        self.account = iam.ServiceAccount(
            name=f"projects/{PROJECT}/serviceAccounts/{CONTEXT.email}",
            project_id=PROJECT,
            unique_id=CONTEXT.service_account_unique_id,
            email=CONTEXT.email,
            description=CONTEXT.owner_description,
        )
        self.role = iam.Role(name=ROLE, stage=2, included_permissions=["aiplatform.endpoints.predict"])
        self.policies = {
            resource: policy_pb2.Policy(
                version=3,
                etag=b"initial",
                bindings=[
                    policy_pb2.Binding(
                        role="roles/viewer",
                        members=["user:foreign@example.invalid"],
                        condition={
                            "title": "preserve",
                            "expression": 'request.time < timestamp("2030-01-01T00:00:00Z")',
                        },
                    )
                ],
            )
            for resource in (ENDPOINT, SECOND, CONTEXT.service_account_resource)
        }
        self.clients = (
            manager.ProjectsClient(
                transport=ProjectsGrpcTransport(
                    channel=lambda *a, **k: self,
                    host="cloudresourcemanager.googleapis.com",
                    credentials=AnonymousCredentials(),
                )
            ),
            iam.IAMClient(
                transport=IAMGrpcTransport(
                    channel=lambda *a, **k: self, host="iam.googleapis.com", credentials=AnonymousCredentials()
                )
            ),
            vertex.EndpointServiceClient(
                transport=EndpointServiceGrpcTransport(
                    channel=lambda *a, **k: self,
                    host=f"{REGION}-aiplatform.googleapis.com",
                    credentials=AnonymousCredentials(),
                )
            ),
        )

    def subscribe(self, *a, **k):
        raise AssertionError("no network")

    unsubscribe = subscribe
    unary_stream = subscribe
    stream_unary = subscribe
    stream_stream = subscribe

    def close(self):
        self.closed += 1

    def unary_unary(self, method, request_serializer=None, response_deserializer=None, *args, **kwargs):
        name = (method.decode() if isinstance(method, bytes) else method).rsplit("/", 1)[-1]
        types = {
            "GetProject": manager.GetProjectRequest,
            "GetServiceAccount": iam.GetServiceAccountRequest,
            "GetRole": iam.GetRoleRequest,
            "GetEndpoint": vertex.GetEndpointRequest,
            "GetIamPolicy": iam_policy_pb2.GetIamPolicyRequest,
            "SetIamPolicy": iam_policy_pb2.SetIamPolicyRequest,
        }

        def call(request, **kwargs):
            assert name in types, "No project IAM, identity creation, listing, or paid allocation"
            request_type = types[name]
            raw = request_serializer(request)
            typed = (
                request_type.deserialize(raw) if hasattr(request_type, "deserialize") else request_type.FromString(raw)
            )
            self.calls.append((name, typed))
            assert 0 < kwargs["timeout"] <= 10
            if self.error:
                raise self.error
            if name == "GetProject":
                assert typed.name == f"projects/{NUMBER}"
                response = self.project
            elif name == "GetServiceAccount":
                assert typed.name == CONTEXT.service_account_resource
                response = self.account
            elif name == "GetRole":
                assert typed.name == ROLE
                response = self.role
            elif name == "GetEndpoint":
                assert typed.name in (ENDPOINT, SECOND)
                response = vertex.Endpoint(name=typed.name)
            elif name == "GetIamPolicy":
                assert typed.options.requested_policy_version == 3
                response = self.policies[typed.resource]
            else:
                assert set(typed.update_mask.paths) == {"bindings", "etag", "version"}
                current = self.policies[typed.resource]
                if typed.policy.etag != current.etag:
                    raise Aborted("native private canary")
                self.writes += 1
                response = policy_pb2.Policy()
                response.CopyFrom(typed.policy)
                response.etag = str(self.writes).encode()
                self.policies[typed.resource] = response
                if self.lost_reply:
                    self.lost_reply = False
                    raise ServiceUnavailable("native private canary")
            if self.after_call:
                self.after_call(name)
            raw = (
                type(response).serialize(response)
                if hasattr(type(response), "serialize")
                else response.SerializeToString()
            )
            return response_deserializer(raw)

        class CompletedCall:
            def trailing_metadata(self):
                return ()

        call.with_call = lambda request, **kwargs: (call(request, **kwargs), CompletedCall())
        return call


@pytest.fixture
def world():
    wire = Wire()
    state = {"ledger": OwnedGrantLedger(CONTEXT.fingerprint), "checkpoints": 0, "admitted": True, "journals": []}

    def checkpoint():
        state["checkpoints"] += 1
        if not state["admitted"]:
            raise NativeIdentityError("CURRENT_AUTHORITY_WITHDRAWN")

    def persist(ledger):
        state["ledger"] = ledger
        state["journals"].append(ledger)

    driver = NativeGCPIdentity(CONTEXT, clients=wire.clients)

    def reconcile(permissions=PERMISSIONS, uids=(UID,)):
        return driver.reconcile(
            permissions, service_account_uids=uids, ledger=state["ledger"], checkpoint=checkpoint, persist=persist
        )

    return wire, state, reconcile


def grants(policy):
    return {
        (binding.role, member)
        for binding in policy.bindings
        if not binding.HasField("condition")
        for member in binding.members
    }


def test_actual_endpoint_and_uid_trust_union_readback_then_remove_preserves_foreign(world):
    wire, state, reconcile = world
    initial = {resource: policy.bindings[0].SerializeToString() for resource, policy in wire.policies.items()}
    result = reconcile()
    assert not result.workload_ready and result.annotations == {"iam.gke.io/gcp-service-account": CONTEXT.email}
    assert (ROLE, "serviceAccount:" + CONTEXT.email) in grants(wire.policies[ENDPOINT])
    assert ("roles/iam.workloadIdentityUser", CONTEXT.principal(UID)) in grants(
        wire.policies[CONTEXT.service_account_resource]
    )
    assert all(
        policy.bindings[0].SerializeToString() == initial[resource] for resource, policy in wire.policies.items()
    )
    assert not result.ledger.pending and len(result.ledger.policies) == 2
    assert state["checkpoints"] > 10 and any(row.pending for row in state["journals"])
    before = wire.writes
    assert reconcile().ledger == result.ledger and wire.writes == before
    result = reconcile([], ())
    assert not result.ledger.policies and not result.ledger.pending
    assert not grants(wire.policies[ENDPOINT]) and not grants(wire.policies[CONTEXT.service_account_resource])
    assert all(
        policy.bindings[0].SerializeToString() == initial[resource] for resource, policy in wire.policies.items()
    )


def test_removing_one_endpoint_preserves_other_current_consumers(world):
    wire, state, reconcile = world
    both = [*PERMISSIONS, {"role": ROLE, "resource": SECOND}]
    reconcile(both)
    reconcile([{"role": ROLE, "resource": SECOND}])
    assert not grants(wire.policies[ENDPOINT])
    assert (ROLE, "serviceAccount:" + CONTEXT.email) in grants(wire.policies[SECOND])
    assert len(state["ledger"].policies) == 2


def test_preexisting_equivalent_foreign_grant_is_unowned_and_never_removed(world):
    wire, _state, reconcile = world
    wire.policies[ENDPOINT].bindings.add(role=ROLE, members=["serviceAccount:" + CONTEXT.email])
    result = reconcile()
    assert result.external_equivalent_grants == 1
    assert all(row.resource != ENDPOINT for row in result.ledger.policies)
    reconcile([], ())
    assert (ROLE, "serviceAccount:" + CONTEXT.email) in grants(wire.policies[ENDPOINT])


@pytest.mark.parametrize("removal", [False, True])
def test_lost_set_reply_replays_durable_intent_without_duplicate_write(world, removal):
    wire, state, reconcile = world
    if removal:
        reconcile()
    wire.lost_reply = True
    args = ([], ()) if removal else (PERMISSIONS, (UID,))
    with pytest.raises(NativeIdentityError, match="UNCONFIRMED"):
        reconcile(*args)
    assert state["ledger"].pending
    writes = wire.writes
    result = reconcile(*args)
    assert not result.ledger.pending
    assert wire.writes <= writes + 1
    if removal:
        assert not result.ledger.policies


def test_pending_lost_reply_with_changed_foreign_policy_refuses_adoption(world):
    wire, state, reconcile = world
    wire.lost_reply = True
    with pytest.raises(NativeIdentityError):
        reconcile()
    resource = state["ledger"].pending[0].resource
    wire.policies[resource].bindings.add(role="roles/viewer", members=["user:other@example.invalid"])
    before = wire.writes
    with pytest.raises(NativeIdentityError, match="PENDING_POLICY_CONFLICT"):
        reconcile()
    assert wire.writes == before


@pytest.mark.parametrize("change", ["unique_id", "description", "email", "disabled", "project_number", "project_id"])
def test_foreign_replaced_or_unowned_identity_refuses_before_effect(world, change):
    wire, state, reconcile = world
    if change == "project_number":
        wire.project.name = "projects/987654321"
    elif change == "project_id":
        wire.project.project_id = "other-project"
    else:
        setattr(wire.account, change, True if change == "disabled" else "foreign")
    with pytest.raises(NativeIdentityError):
        reconcile()
    assert not wire.writes and not state["journals"]


@pytest.mark.parametrize(
    "permissions",
    [
        [{"role": "roles/aiplatform.user", "resource": ENDPOINT}],
        [{"role": ROLE, "resource": f"projects/{NUMBER}"}],
        [{"role": ROLE, "resource": ENDPOINT.replace(NUMBER, "999999999")}],
        [{"role": ROLE, "resource": ENDPOINT.replace(REGION, "us-east1")}],
        [{"role": ROLE}],
    ],
)
def test_invalid_or_broad_permissions_never_fall_back_to_project(world, permissions):
    wire, _state, reconcile = world
    with pytest.raises(NativeIdentityError):
        reconcile(permissions)
    assert not wire.writes and not wire.calls


@pytest.mark.parametrize("change", ["broader", "deleted", "disabled"])
def test_custom_prediction_role_requires_current_exact_permissions(world, change):
    wire, _state, reconcile = world
    if change == "broader":
        wire.role.included_permissions.append("aiplatform.endpoints.deploy")
    elif change == "deleted":
        wire.role.deleted = True
    else:
        wire.role.stage = 5
    with pytest.raises(NativeIdentityError, match="PREDICTION_ROLE_UNVERIFIED"):
        reconcile()
    assert not wire.writes


def test_uid_principal_never_trusts_a_namespace_name_or_caller_supplied_subject(world):
    wire, _state, reconcile = world
    with pytest.raises(NativeIdentityError):
        reconcile(uids=("namespace/sa",))
    with pytest.raises(NativeIdentityError):
        reconcile(uids=(UID, UID))
    assert not wire.writes and not wire.calls


def test_actual_endpoint_etag_conflict_retains_intent_and_preserves_foreign_edit(world):
    wire, state, reconcile = world

    def after(name):
        if name == "GetServiceAccount" and state["ledger"].pending:
            resource = state["ledger"].pending[0].resource
            wire.policies[resource].etag = b"concurrent"
            wire.policies[resource].bindings.add(role="roles/viewer", members=["user:concurrent@example.invalid"])
            wire.after_call = None

    wire.after_call = after
    with pytest.raises(NativeIdentityError, match="UNCONFIRMED"):
        reconcile()
    assert not wire.writes and state["ledger"].pending
    assert any(
        "user:concurrent@example.invalid" in binding.members
        for policy in wire.policies.values()
        for binding in policy.bindings
    )


def test_authority_withdrawn_after_native_response_refuses_write(world):
    wire, state, reconcile = world
    wire.after_call = lambda name: state.update(admitted=False) if name == "GetIamPolicy" else None
    with pytest.raises(NativeIdentityError, match="CURRENT_AUTHORITY_WITHDRAWN"):
        reconcile()
    assert not wire.writes


def test_current_gsa_identity_rechecked_after_journal_before_write(world):
    wire, state, reconcile = world

    def after(name):
        if name == "GetServiceAccount" and state["ledger"].pending:
            wire.account.unique_id = "110099999999999999999"

    wire.after_call = after
    with pytest.raises(NativeIdentityError, match="OWNERSHIP_UNVERIFIED"):
        reconcile()
    assert not wire.writes


@pytest.mark.parametrize("problem", ["etag", "version", "members"])
def test_incomplete_or_oversized_policy_is_not_treated_as_empty(world, problem):
    wire, _state, reconcile = world
    policy = wire.policies[ENDPOINT]
    if problem == "etag":
        policy.etag = b""
    elif problem == "version":
        policy.version = 1
    else:
        policy.bindings[0].members.extend(["user:many@example.invalid"] * 1024)
    with pytest.raises(NativeIdentityError):
        reconcile()
    assert not wire.writes


def test_denied_transport_private_error_is_sanitized_without_success(world):
    wire, _state, reconcile = world
    wire.error = PermissionDenied("credential-private-canary")
    with pytest.raises(NativeIdentityError) as error:
        reconcile()
    assert "canary" not in str(error.value) and not wire.writes


def test_journal_failure_prevents_provider_write(world):
    wire, state, _reconcile = world

    def fail(ledger):
        raise RuntimeError("durable journal unavailable")

    with pytest.raises(RuntimeError, match="journal"):
        NativeGCPIdentity(CONTEXT, clients=wire.clients).reconcile(
            PERMISSIONS, service_account_uids=(UID,), ledger=state["ledger"], checkpoint=lambda: None, persist=fail
        )
    assert not wire.writes


def test_legacy_endpoint_grant_refuses_before_service_account_creation():
    class Never:
        def create_service_account(self, **kwargs):
            pytest.fail("unsafe legacy effect")

    driver = GCPWorkloadIdentityDriver(config=GCPWIConfig(PROJECT, iam_client=Never()))
    with pytest.raises(ValueError, match="resource-scoped"):
        driver.create_identity_role("legacy", [{"role": "roles/aiplatform.user", "resource": ENDPOINT}])


def test_private_factory_uses_fixed_hosts_actual_sdk_and_preserves_global_logger(monkeypatch, caplog):
    import google.auth

    wire = Wire()
    hosts = []

    def channel(host, **kwargs):
        hosts.append(host)
        assert kwargs["options"] == [("grpc.max_receive_message_length", 2 * 1024 * 1024)]
        return wire

    for transport in (ProjectsGrpcTransport, IAMGrpcTransport, EndpointServiceGrpcTransport):
        monkeypatch.setattr(transport, "create_channel", channel)
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (AnonymousCredentials(), PROJECT))
    monkeypatch.setenv("GOOGLE_API_USE_MTLS_ENDPOINT", "always")
    caplog.set_level("DEBUG")
    with NativeGCPIdentity(CONTEXT) as driver:
        result = driver.reconcile(
            PERMISSIONS,
            service_account_uids=(UID,),
            ledger=OwnedGrantLedger(CONTEXT.fingerprint),
            checkpoint=lambda: None,
            persist=lambda ledger: None,
        )
    assert not result.workload_ready
    assert hosts == ["cloudresourcemanager.googleapis.com", "iam.googleapis.com", f"{REGION}-aiplatform.googleapis.com"]
    assert wire.closed == 3
    assert CONTEXT.email not in caplog.text and "foreign@example.invalid" not in caplog.text


def test_foreign_ledger_context_cannot_be_reused(world):
    wire, state, reconcile = world
    state["ledger"] = replace(state["ledger"], context_sha256="0" * 64)
    with pytest.raises(NativeIdentityError, match="INVALID_LEDGER_CONTEXT"):
        reconcile()
    assert not wire.calls


def test_role_broadened_after_policy_read_refuses_at_final_effect_checkpoint(world):
    wire, _state, reconcile = world

    def after(name):
        if name == "GetIamPolicy":
            wire.role.included_permissions.append("aiplatform.endpoints.deploy")
            wire.after_call = None

    wire.after_call = after
    with pytest.raises(NativeIdentityError, match="PREDICTION_ROLE_UNVERIFIED"):
        reconcile()
    assert not wire.writes


def test_endpoint_permission_without_current_ksa_uids_cannot_grant_access(world):
    wire, _state, reconcile = world
    with pytest.raises(NativeIdentityError, match="INVALID_PRINCIPAL_SET"):
        reconcile(uids=())
    assert not wire.calls


def test_legacy_aiplatform_user_without_resource_never_receives_project_scope():
    class Never:
        def create_service_account(self, **kwargs):
            pytest.fail("unsafe legacy effect")

    driver = GCPWorkloadIdentityDriver(config=GCPWIConfig(PROJECT, iam_client=Never()))
    with pytest.raises(ValueError, match="resource-scoped"):
        driver.create_identity_role("legacy", [{"role": "roles/aiplatform.user"}])


def test_full_policy_audit_and_empty_foreign_binding_survive_owned_pair_changes(world):
    wire, _state, reconcile = world
    policy = wire.policies[ENDPOINT]
    policy.bindings.add(role="roles/logging.viewer")
    policy.audit_configs.add(service="aiplatform.googleapis.com", audit_log_configs=[{"log_type": "DATA_READ"}])
    audit = policy.audit_configs[0].SerializeToString()
    reconcile()
    reconcile([], ())
    current = wire.policies[ENDPOINT]
    assert current.audit_configs[0].SerializeToString() == audit
    assert any(binding.role == "roles/logging.viewer" and not binding.members for binding in current.bindings)


def test_successful_write_with_unverified_readback_retains_pending_not_success(world):
    wire, state, reconcile = world

    def after(name):
        if name == "SetIamPolicy":
            resource = state["ledger"].pending[0].resource
            wire.policies[resource].bindings.add(role="roles/viewer", members=["user:unexpected@example.invalid"])
            wire.after_call = None

    wire.after_call = after
    with pytest.raises(NativeIdentityError, match="POLICY_READBACK_MISMATCH"):
        reconcile()
    assert wire.writes == 1 and state["ledger"].pending
