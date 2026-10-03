"""Native Kubernetes/boto3 HTTP proof; no live ingestion or persisted operation claim."""

from __future__ import annotations

import copy
import json
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
import pytest
import yaml
from botocore.config import Config
from kubernetes import client
from kubernetes.dynamic import DynamicClient

from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from _sdk.observability.cloudwatch_logs import CloudWatchLogsConfig, CloudWatchLogsQueryDriver
from aws._cloudwatch_collector import NAMESPACE
from aws.cloudwatch_collector import CollectorStage
from aws.cloudwatch_collector_execution import (
    OWNER,
    CollectorExecutor,
    ExecutionRefused,
    ExecutionRequest,
    ReaderBinding,
    ReaderGrantPending,
)

GUID = "f7bcbeb4-0c12-4a2c-ac53-ecbc62909d1b"
OP = "b7bcbeb4-0c12-4a2c-ac53-ecbc62909d1b"
STAGE = CollectorStage(
    GUID,
    "arn:aws:eks:us-west-2:123456789012:cluster/fixture",
    "us-west-2",
    "/astrolift/fixture",
    "arn:aws:logs:us-west-2:123456789012:log-group:/astrolift/fixture",
    "arn:aws:iam::123456789012:role/astrolift/fluent-bit",
    "https://oidc.eks.us-west-2.amazonaws.com/id/fixture",
)
REQUEST = ExecutionRequest(
    OP,
    "2026-10-03T00:00:00Z",
    "2026-10-03T00:10:00Z",
    "docker.io/library/busybox@sha256:" + "a" * 64,
    "registered-source-v1",
)
RESOURCE_INFO = {
    "namespaces": ("Namespace", False),
    "serviceaccounts": ("ServiceAccount", True),
    "configmaps": ("ConfigMap", True),
    "services": ("Service", True),
    "pods": ("Pod", True),
    "nodes": ("Node", False),
    "daemonsets": ("DaemonSet", True),
    "clusterroles": ("ClusterRole", False),
    "clusterrolebindings": ("ClusterRoleBinding", False),
}


class Port:
    def __init__(self, wire):
        self.wire, self.state, self.actions, self.deny = wire, {}, [], None

    def check(self, action):
        self.actions.append(action)
        self.wire["last_check"] = action
        if self.deny and self.deny(action):
            raise ExecutionRefused("AUTHORITY_REVOKED")

    def load(self):
        return copy.deepcopy(self.state)

    def save(self, state):
        self.state = copy.deepcopy(state)


@pytest.fixture(scope="module")
def artifact(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("collector-render")
    subprocess.run(
        [
            "helm",
            "pull",
            "fluent-bit",
            "--repo",
            "https://fluent.github.io/helm-charts",
            "--version",
            "0.58.2",
            "--destination",
            str(scratch),
        ],
        check=True,
        capture_output=True,
    )
    archive = scratch / "fluent-bit-0.58.2.tgz"
    values = scratch / "values.yaml"
    values.write_text(yaml.safe_dump(STAGE.component().helm_values))
    result = subprocess.run(
        ["helm", "template", "fluent-bit", str(archive), "-n", NAMESPACE, "-f", str(values)],
        check=True,
        capture_output=True,
        text=True,
    )
    manifests = [item for item in yaml.safe_load_all(result.stdout) if item]
    return archive.read_bytes(), manifests


@pytest.fixture
def wire(tmp_path):
    state = {
        "objects": {},
        "calls": [],
        "last_check": None,
        "sequence": 0,
        "ready": True,
        "ingest": True,
        "reader_error": False,
        "stall": threading.Event(),
        "stall_nodes": False,
        "delete_conflict": False,
        "lost_create": False,
        "lost_create_kind": None,
        "lose_after_delete": False,
        "lost_delete": False,
        "nodes": [
            {
                "apiVersion": "v1",
                "kind": "Node",
                "metadata": {
                    "name": "ec2-a",
                    "uid": "node-uid",
                    "resourceVersion": "1",
                    "labels": {"kubernetes.io/os": "linux"},
                },
                "spec": {"providerID": "aws:///us-west-2a/i-0123456789abcdef0"},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        ],
    }
    state["objects"]["/api/v1/namespaces/astrolift-system"] = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": NAMESPACE,
            "uid": "shared-ns",
            "resourceVersion": "1",
            "labels": {"unrelated": "preserved"},
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, payload, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with suppress(BrokenPipeError):
                self.wfile.write(json.dumps(payload).encode())

        def failure(self, code):
            self.send(
                {
                    "apiVersion": "v1",
                    "kind": "Status",
                    "status": "Failure",
                    "reason": "Conflict" if code == 409 else "NotFound",
                    "code": code,
                },
                code,
            )

        def record(self, body=None):
            state["calls"].append((self.command, self.path.split("?")[0], copy.deepcopy(body), state["last_check"]))

        def do_GET(self):
            self.record()
            path = self.path.split("?")[0]
            if path == "/version":
                return self.send({"major": "1", "minor": "31", "gitVersion": "v1.31.0"})
            if path == "/api":
                return self.send({"kind": "APIVersions", "versions": ["v1"]})
            if path == "/apis":
                return self.send(
                    {
                        "kind": "APIGroupList",
                        "groups": [
                            {
                                "name": g,
                                "versions": [{"groupVersion": g + "/v1", "version": "v1"}],
                                "preferredVersion": {"groupVersion": g + "/v1", "version": "v1"},
                            }
                            for g in ("apps", "rbac.authorization.k8s.io")
                        ],
                    }
                )
            if path in {"/api/v1", "/apis/apps/v1", "/apis/rbac.authorization.k8s.io/v1"}:
                names = {
                    "/api/v1": ["namespaces", "serviceaccounts", "configmaps", "services", "pods", "nodes"],
                    "/apis/apps/v1": ["daemonsets"],
                    "/apis/rbac.authorization.k8s.io/v1": ["clusterroles", "clusterrolebindings"],
                }[path]
                return self.send(
                    {
                        "kind": "APIResourceList",
                        "groupVersion": path.replace("/api/", "").replace("/apis/", ""),
                        "resources": [
                            {
                                "name": n,
                                "kind": RESOURCE_INFO[n][0],
                                "namespaced": RESOURCE_INFO[n][1],
                                "verbs": ["get", "list", "create", "delete", "patch"],
                            }
                            for n in names
                        ],
                    }
                )
            if path == "/api/v1/nodes":
                if state["stall_nodes"]:
                    state["stall"].wait(timeout=25)
                return self.send({"apiVersion": "v1", "kind": "NodeList", "items": state["nodes"]})
            obj = copy.deepcopy(state["objects"].get(path))
            if obj is None:
                return self.failure(404)
            if obj["kind"] == "DaemonSet":
                count = len(
                    [
                        n
                        for n in state["nodes"]
                        if n.get("spec", {}).get("providerID", "").startswith("aws://")
                        and n["metadata"]["labels"].get("kubernetes.io/os") == "linux"
                        and n["metadata"]["labels"].get("eks.amazonaws.com/compute-type") != "fargate"
                    ]
                )
                obj["status"] = {
                    "observedGeneration": 1 if state["ready"] else 0,
                    "desiredNumberScheduled": count,
                    "updatedNumberScheduled": count,
                    "numberReady": count,
                    "numberAvailable": count,
                    "numberUnavailable": 0,
                }
            return self.send(obj)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.record(body)
            if self.headers.get("X-Amz-Target"):
                if state["reader_error"]:
                    return self.send({"__type": "AccessDeniedException", "message": "PRIVATE_PROVIDER_BODY"}, 400)
                probe = next((o for o in state["objects"].values() if o["kind"] == "Pod"), None) or state.get(
                    "deleted_probe"
                )
                events = []
                if probe and state["ingest"] and not (state.get("deleted_probe") and state["lose_after_delete"]):
                    marker = probe["spec"]["containers"][0]["env"][0]["value"]
                    meta = {
                        "namespace_name": NAMESPACE,
                        "pod_name": probe["metadata"]["name"],
                        "container_name": "probe",
                        "labels": probe["metadata"]["labels"],
                    }
                    events = [
                        {
                            "eventId": "fixture-event",
                            "timestamp": 1790985630000,
                            "logStreamName": "from-fluent-bit-fixture",
                            "message": json.dumps({"log": marker + "\n", "kubernetes": meta}),
                        },
                        {
                            "eventId": "foreign-event",
                            "timestamp": 1790985630000,
                            "message": json.dumps({"log": marker, "kubernetes": {**meta, "namespace_name": "foreign"}}),
                        },
                    ]
                return self.send({"events": events})
            path = self.path.split("?")[0] + "/" + body["metadata"]["name"]
            if path in state["objects"]:
                return self.failure(409)
            state["sequence"] += 1
            body["metadata"].update(
                uid="uid-" + str(state["sequence"]), resourceVersion=str(state["sequence"]), generation=1
            )
            state["objects"][path] = body
            if state["lost_create"] and (
                state["lost_create_kind"] is None or state["lost_create_kind"] == body["kind"]
            ):
                state["lost_create"] = False
                return self.send({"kind": "Status", "code": 500, "message": "PRIVATE_PROVIDER_BODY"}, 500)
            return self.send(body, 201)

        def do_DELETE(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.record(body)
            path = self.path.split("?")[0]
            obj = state["objects"].get(path)
            if obj is None:
                return self.failure(404)
            if state["delete_conflict"] or any(obj["metadata"][k] != v for k, v in body["preconditions"].items()):
                return self.failure(409)
            state["deleted_probe"] = state["objects"].pop(path)
            if state["lost_delete"]:
                state["lost_delete"] = False
                return self.send({"kind": "Status", "code": 500, "message": "PRIVATE_PROVIDER_BODY"}, 500)
            return self.send({"kind": "Status", "status": "Success"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    config = client.Configuration()
    config.host = endpoint
    config.api_key = {"authorization": "Bearer local-fixture"}
    config.proxy = None
    api = client.ApiClient(config)
    kube = KubernetesDynamicClient.from_api_client(api_client=api)
    kube._dynamic = DynamicClient(api, cache_file=str(tmp_path / "discovery.json"))
    logs = boto3.client(
        "logs",
        region_name=STAGE.region,
        endpoint_url=endpoint,
        aws_access_key_id="local-fixture",
        aws_secret_access_key="local-fixture",
        config=Config(retries={"max_attempts": 0}, proxies={}),
    )
    state["kube"], state["port"], state["executors"] = kube, Port(state), []
    query = CloudWatchLogsQueryDriver(
        config=CloudWatchLogsConfig(log_group=STAGE.log_group, region=STAGE.region, client=logs)
    )
    state["reader"] = lambda stage: ReaderBinding(
        query, stage.cluster_guid, stage.log_group, stage.region, REQUEST.reader_source
    )
    state["calls"].clear()
    try:
        yield state
    finally:
        state["stall"].set()
        for executor in state["executors"]:
            executor.close()
        logs.close()
        api.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def execution(wire, artifact, **overrides):
    archive, manifests = artifact
    args = {
        "stage": STAGE,
        "request": REQUEST,
        "archive": archive,
        "renderer": lambda *_args, **_kwargs: copy.deepcopy(manifests),
        "kubernetes": wire["kube"],
        "reader_factory": wire["reader"],
        "checkpoints": wire["port"],
        "idle": lambda: None,
    }
    args.update(overrides)
    executor = CollectorExecutor(**args)
    wire["executors"].append(executor)
    return executor


def test_native_conditional_resources_scoped_read_exact_probe_delete_and_fresh_post_loss_read(wire, artifact):
    result = execution(wire, artifact).run()
    assert result.state == "POST_LOSS_READ_VERIFIED"
    assert result.coverage == "LINUX_EC2" and result.event_hash and not result.cleanup_pending
    assert result.reader_activation_performed is False
    creates = [c for c in wire["calls"] if c[0] == "POST" and c[1] != "/"]
    assert creates and all(c[3] == "kubernetes.http.POST" for c in creates)
    assert all(c[2]["metadata"]["annotations"][OWNER] == OP for c in creates)
    shared = wire["objects"]["/api/v1/namespaces/astrolift-system"]
    assert shared["metadata"]["labels"] == {"unrelated": "preserved"} and OWNER not in shared["metadata"].get(
        "annotations", {}
    )
    [deleted] = [c for c in wire["calls"] if c[0] == "DELETE"]
    assert deleted[3] == "kubernetes.http.DELETE"
    assert deleted[2]["preconditions"] == {k: wire["deleted_probe"]["metadata"][k] for k in ("uid", "resourceVersion")}
    reads = [c for c in wire["calls"] if c[0] == "POST" and c[1] == "/"]
    assert len(reads) == 3 and all(c[3] == "reader.query" for c in reads)
    assert all(c[2]["logGroupName"] == STAGE.log_group and "nextToken" not in c[2] for c in reads)
    assert all(
        'namespace_name = "astrolift-system"' in c[2]["filterPattern"] and "collector-probe" in c[2]["filterPattern"]
        for c in reads
    )
    assert max(i for i, c in enumerate(wire["calls"]) if c[0] == "POST" and c[1] == "/") > wire["calls"].index(deleted)
    receipt = json.dumps(wire["port"].state)
    assert "astrolift-collector-proof-" not in receipt and "PRIVATE_PROVIDER_BODY" not in receipt
    wire["calls"].clear()
    assert execution(wire, artifact).run().state == "POST_LOSS_READ_VERIFIED"
    assert not any(c[0] in {"DELETE", "POST"} and c[1] != "/" for c in wire["calls"])


@pytest.mark.parametrize("setting,expected", [("ready", "READINESS_PENDING"), ("ingest", "INGESTION_PENDING")])
def test_bounded_polls_and_retry_retains_original_probe(wire, artifact, setting, expected):
    wire[setting] = False
    result = execution(wire, artifact).run()
    assert result.state == expected
    assert len([a for a in wire["port"].actions if a.startswith("idle.")]) == 2
    old = copy.deepcopy(wire["port"].state)
    wire[setting] = True
    assert execution(wire, artifact).run().state == "POST_LOSS_READ_VERIFIED"
    if setting == "ingest":
        key = next(k for k in old["resources"] if k.startswith("v1/Pod/"))
        assert wire["port"].state["resources"][key]["uid"] == old["resources"][key]["uid"]


def test_lost_create_recovers_only_persisted_intent_and_exact_owned_resource(wire, artifact):
    wire["lost_create"] = True
    assert execution(wire, artifact).run().state == "PROVIDER_OUTCOME_UNCERTAIN"
    writes = [c for c in wire["calls"] if c[0] == "POST"]
    assert len(writes) == 1
    created = next(o for o in wire["objects"].values() if o["kind"] != "Namespace")
    assert execution(wire, artifact).run().state == "POST_LOSS_READ_VERIFIED"
    assert (
        wire["objects"][next(k for k, v in wire["objects"].items() if v["kind"] == created["kind"])]["metadata"]["uid"]
        == created["metadata"]["uid"]
    )


def test_refused_delete_never_retries_without_original_uid_and_rv(wire, artifact):
    wire["delete_conflict"] = True
    with pytest.raises(ExecutionRefused, match="PROBE_DELETE_PRECONDITION_FAILED"):
        execution(wire, artifact).run()
    key = next(k for k in wire["objects"] if "/pods/" in k)
    pod = wire["objects"][key]
    pod["metadata"]["uid"] = "replacement"
    with pytest.raises(ExecutionRefused, match="PROBE_DELETE_PRECONDITION_FAILED"):
        execution(wire, artifact).run()
    assert len([c for c in wire["calls"] if c[0] == "DELETE"]) == 1
    assert wire["objects"][key]["metadata"]["uid"] == "replacement"


def test_deletion_does_not_certify_retention_without_new_read(wire, artifact):
    wire["lose_after_delete"] = True
    result = execution(wire, artifact).run()
    assert result.state == "POST_LOSS_READ_PENDING" and not result.cleanup_pending
    assert wire["port"].state["probe_deleted"] and result.event_hash is None


def test_explicit_reader_grant_pending_or_sanitized_uncertain(wire, artifact):
    def pending(_):
        raise ReaderGrantPending

    assert execution(wire, artifact, reader_factory=pending).run().state == "READER_GRANT_PENDING"
    assert not any(o["kind"] == "Pod" for o in wire["objects"].values())
    wire["reader_error"] = True
    result = execution(wire, artifact).run()
    assert result.state == "PROVIDER_OUTCOME_UNCERTAIN" and not result.cleanup_pending
    assert "PRIVATE_PROVIDER_BODY" not in repr(result)
    assert not any(o["kind"] == "Pod" for o in wire["objects"].values())


@pytest.mark.parametrize("mode", ["fargate", "windows", "unknown"])
def test_unsupported_coverage_before_any_resource_creation(wire, artifact, mode):
    node = wire["nodes"][0]
    if mode == "fargate":
        node["metadata"]["labels"]["eks.amazonaws.com/compute-type"] = "fargate"
    elif mode == "windows":
        node["metadata"]["labels"]["kubernetes.io/os"] = "windows"
    else:
        node["spec"]["providerID"] = "unknown"
    with pytest.raises(ExecutionRefused, match="UNSUPPORTED_NODE_COVERAGE"):
        execution(wire, artifact).run()
    assert not any(c[0] in {"POST", "DELETE", "PATCH"} for c in wire["calls"])


def test_mixed_cluster_truthfully_reports_ec2_only(wire, artifact):
    wire["nodes"].append(
        {
            "metadata": {
                "name": "fargate",
                "labels": {"kubernetes.io/os": "linux", "eks.amazonaws.com/compute-type": "fargate"},
            },
            "spec": {"providerID": "aws:///fargate"},
        }
    )
    assert execution(wire, artifact).run().coverage == "EC2_ONLY_MIXED"
    assert wire["deleted_probe"]["spec"]["nodeName"] == "ec2-a"


def test_authority_refusal_before_effect_or_idle_poll(wire, artifact):
    wire["port"].deny = lambda action: action == "kubernetes.create"
    with pytest.raises(ExecutionRefused, match="AUTHORITY_REVOKED"):
        execution(wire, artifact).run()
    assert not any(c[0] == "POST" for c in wire["calls"])
    wire["port"].deny = lambda action: action == "idle.readiness"
    wire["ready"] = False
    with pytest.raises(ExecutionRefused, match="AUTHORITY_REVOKED"):
        execution(wire, artifact).run()
    assert not any(o["kind"] == "Pod" for o in wire["objects"].values())


def test_foreign_collision_and_recorded_collector_replacement_refuse(wire, artifact):
    executor = execution(wire, artifact)
    wire["ready"] = False
    assert executor.run().state == "READINESS_PENDING"
    key = next(k for k, o in wire["objects"].items() if o["kind"] == "ServiceAccount")
    wire["objects"][key]["metadata"]["uid"] = "foreign-replacement"
    before = len([c for c in wire["calls"] if c[0] in {"POST", "DELETE"}])
    with pytest.raises(ExecutionRefused, match="RECORDED_RESOURCE_REPLACED"):
        execution(wire, artifact).run()
    assert len([c for c in wire["calls"] if c[0] in {"POST", "DELETE"}]) == before


def test_source_renderer_and_reader_identity_fail_closed(wire, artifact):
    with pytest.raises(ExecutionRefused, match="CHART_DIGEST_MISMATCH"):
        execution(wire, artifact, archive=b"wrong")
    with pytest.raises(ExecutionRefused, match="VERIFIED_RENDERER_UNAVAILABLE"):
        execution(wire, artifact, renderer=None)
    assert wire["calls"] == []
    execution(wire, artifact)
    with pytest.raises(ExecutionRefused, match="CHECKPOINT_SOURCE_CHANGED"):
        execution(wire, artifact, request=replace(REQUEST, reader_source="replacement"))
    binding = wire["reader"](STAGE)
    with pytest.raises(ExecutionRefused, match="REGISTERED_READER_IDENTITY_MISMATCH"):
        execution(wire, artifact, reader_factory=lambda _: replace(binding, source="foreign")).run()
    assert not any(o["kind"] == "Pod" for o in wire["objects"].values())


def test_real_stalled_http_response_has_finite_timeout_and_no_retry(wire, artifact):
    wire["stall_nodes"] = True
    executor = execution(wire, artifact)
    original_retries = wire["kube"]._api_client.configuration.retries
    started = time.monotonic()
    result = executor.run()
    elapsed = time.monotonic() - started
    assert result.state == "PROVIDER_OUTCOME_UNCERTAIN"
    assert 19 <= elapsed < 24
    assert len([c for c in wire["calls"] if c[1] == "/api/v1/nodes"]) == 1
    assert executor.kube._api_client.configuration.retries == 0
    assert wire["kube"]._api_client.configuration.retries == original_retries
    assert executor.kube._api_client is not wire["kube"]._api_client
    assert not any(c[0] in {"POST", "PATCH", "DELETE"} for c in wire["calls"])


def test_gate_holds_before_registered_token_refresh_network_and_http_effect(wire, artifact):
    refreshes = []
    wire["kube"]._token_provider = lambda: refreshes.append("refreshed") or "local-fixture"
    executor = execution(wire, artifact)
    wire["port"].deny = lambda action: action == "kubernetes.refresh"
    with pytest.raises(ExecutionRefused, match="AUTHORITY_REVOKED"):
        executor.run()
    assert not refreshes and not wire["calls"]
    wire["port"].deny = lambda action: action == "kubernetes.http.POST"
    with pytest.raises(ExecutionRefused, match="AUTHORITY_REVOKED"):
        executor.run()
    assert refreshes
    assert not any(c[0] == "POST" for c in wire["calls"])


def test_foreign_name_collision_is_not_adopted_or_overwritten(wire, artifact):
    path = "/api/v1/namespaces/astrolift-system/serviceaccounts/fluent-bit"
    foreign = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": "fluent-bit", "namespace": NAMESPACE, "uid": "foreign", "resourceVersion": "1"},
    }
    wire["objects"][path] = copy.deepcopy(foreign)
    with pytest.raises(ExecutionRefused, match="FOREIGN_OR_CHANGED_RESOURCE"):
        execution(wire, artifact).run()
    assert wire["objects"][path] == foreign
    assert not any(c[0] in {"POST", "DELETE", "PATCH"} for c in wire["calls"])


def test_probe_uid_change_before_delete_refuses_without_targeting_replacement(wire, artifact):
    wire["ingest"] = False
    assert execution(wire, artifact).run().state == "INGESTION_PENDING"
    path = next(k for k in wire["objects"] if "/pods/" in k)
    wire["objects"][path]["metadata"]["uid"] = "replacement"
    wire["ingest"] = True
    with pytest.raises(ExecutionRefused, match="RECORDED_RESOURCE_REPLACED"):
        execution(wire, artifact).run()
    assert not any(c[0] == "DELETE" for c in wire["calls"])


def test_missing_shared_namespace_and_retiring_namespace_refuse_before_any_write(wire, artifact):
    path = "/api/v1/namespaces/astrolift-system"
    namespace = wire["objects"].pop(path)
    with pytest.raises(ExecutionRefused, match="COLLECTOR_NAMESPACE_UNAVAILABLE"):
        execution(wire, artifact).run()
    namespace["metadata"]["deletionTimestamp"] = "2026-10-03T00:00:00Z"
    wire["objects"][path] = namespace
    with pytest.raises(ExecutionRefused, match="RESOURCE_TERMINATING"):
        execution(wire, artifact).run()
    assert not any(c[0] in {"POST", "DELETE", "PATCH"} for c in wire["calls"])


def test_lost_delete_reply_recovers_same_probe_without_recreate_or_second_delete(wire, artifact):
    wire["lost_delete"] = True
    first = execution(wire, artifact).run()
    assert first.state == "PROVIDER_OUTCOME_UNCERTAIN" and first.cleanup_pending
    deleted_uid = wire["deleted_probe"]["metadata"]["uid"]
    assert wire["port"].state["delete_intent"]["uid"] == deleted_uid
    wire["calls"].clear()
    second = execution(wire, artifact).run()
    assert second.state == "POST_LOSS_READ_VERIFIED" and not second.cleanup_pending
    assert not any(c[0] in {"DELETE", "POST"} and c[1] != "/" for c in wire["calls"])


def test_changed_rv_after_delete_conflict_cannot_broaden_a_refused_intent(wire, artifact):
    wire["delete_conflict"] = True
    with pytest.raises(ExecutionRefused, match="PROBE_DELETE_PRECONDITION_FAILED"):
        execution(wire, artifact).run()
    intent = copy.deepcopy(wire["port"].state["delete_intent"])
    wire["delete_conflict"] = False
    path = next(k for k in wire["objects"] if "/pods/" in k)
    wire["objects"][path]["metadata"]["resourceVersion"] = "new-version"
    with pytest.raises(ExecutionRefused, match="PROBE_DELETE_PRECONDITION_FAILED"):
        execution(wire, artifact).run()
    assert wire["port"].state["delete_intent"] == intent
    assert len([c for c in wire["calls"] if c[0] == "DELETE"]) == 1


def test_lost_probe_create_reports_pending_cleanup_and_preserves_original_uid(wire, artifact):
    wire["lost_create"] = True
    wire["lost_create_kind"] = "Pod"
    first = execution(wire, artifact).run()
    assert first.state == "PROVIDER_OUTCOME_UNCERTAIN" and first.cleanup_pending
    path = next(k for k in wire["objects"] if "/pods/" in k)
    uid = wire["objects"][path]["metadata"]["uid"]
    assert wire["port"].state["resources"]["v1/Pod/" + wire["objects"][path]["metadata"]["name"]] == {"intent": True}
    wire["calls"].clear()
    assert execution(wire, artifact).run().state == "POST_LOSS_READ_VERIFIED"
    assert wire["deleted_probe"]["metadata"]["uid"] == uid
    assert not any(c[0] == "POST" and "/pods" in c[1] for c in wire["calls"])


def test_pending_probe_cleanup_remains_truthful_when_later_readiness_is_lost(wire, artifact):
    wire["ingest"] = False
    first = execution(wire, artifact).run()
    assert first.state == "INGESTION_PENDING" and first.cleanup_pending
    pod = next(o for o in wire["objects"].values() if o["kind"] == "Pod")
    wire["ready"] = False
    wire["calls"].clear()
    second = execution(wire, artifact).run()
    assert second.state == "READINESS_PENDING" and second.cleanup_pending
    assert next(o for o in wire["objects"].values() if o["kind"] == "Pod")["metadata"]["uid"] == pod["metadata"]["uid"]
    assert not any(c[0] in {"POST", "DELETE", "PATCH"} for c in wire["calls"])


@pytest.mark.parametrize(
    "provider_id,compute",
    [
        ("aws:///us-west-2a/fargate-192.0.2.1", None),
        ("aws:///us-west-2a/virtual-node", None),
        ("aws:///eu-central-1a/i-0123456789abcdef0", "ec2"),
        ("aws:///us-west-2a/i-0123456789abcdef0", "unknown-runtime"),
    ],
)
def test_opaque_aws_or_foreign_region_node_ids_do_not_prove_ec2_coverage(wire, artifact, provider_id, compute):
    node = wire["nodes"][0]
    node["spec"]["providerID"] = provider_id
    if compute:
        node["metadata"]["labels"]["eks.amazonaws.com/compute-type"] = compute
    with pytest.raises(ExecutionRefused, match="UNSUPPORTED_NODE_COVERAGE"):
        execution(wire, artifact).run()
    assert not any(c[0] in {"POST", "DELETE", "PATCH"} for c in wire["calls"])
