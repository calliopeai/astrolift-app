"""Actual GKE/CRM GAPIC and owned Kubernetes TLS rollout reads; no cloud."""

import copy
import json
import logging
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

import pytest
from google.api_core.exceptions import PermissionDenied
from google.oauth2.credentials import Credentials

from gcp import gke_identity_observation as source
from gcp import gke_identity_runtime as module
from gcp.gke_identity_runtime import (
    GKEIdentityRuntimeObserver,
    KubernetesRuntimeAdapter,
    WorkloadTarget,
    placement_sha256,
    template_sha256,
)

from .test_gke_identity_observation_2278 import (  # noqa: F401
    CONTEXT,
    IDENTITY,
    SUBJECT,
    Kube,
    Wire,
    tls_server,
)

KINDS = ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob")


def metadata(name, uid, kind, *, namespace=SUBJECT.namespace):
    return {
        "apiVersion": module._ROUTES[kind][0] if kind in module._ROUTES else "v1",
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid,
            "resourceVersion": "19",
            "labels": {**CONTEXT.owner_labels, "app": "owned-test"},
        },
    }


def owner(kind, name, uid):
    return [{"apiVersion": module._ROUTES[kind][0], "kind": kind, "name": name, "uid": uid, "controller": True}]


class RuntimeKube(Kube):
    def __init__(self, kind="Deployment"):
        super().__init__()
        self.kind, self.uid = kind, str(uuid4())
        self.template = {
            "metadata": {"labels": {**CONTEXT.owner_labels, "app": "owned-test"}},
            "spec": {
                "serviceAccountName": SUBJECT.name,
                "containers": [{"name": "app", "image": "image@sha256:" + "a" * 64, "args": ["original"]}],
                "nodeSelector": {"iam.gke.io/gke-metadata-server-enabled": "true"},
            },
        }
        if kind in ("Job", "CronJob"):
            self.template["spec"]["restartPolicy"] = "Never"
        self.selector = {"matchLabels": {"app": "owned-test"}}
        self.controller = metadata("owned-controller", self.uid, kind)
        self.controller["metadata"]["generation"] = 2
        self.controller["spec"] = {"template": copy.deepcopy(self.template), "selector": self.selector, "replicas": 1}
        self.controller["status"] = {
            "observedGeneration": 2,
            "replicas": 1,
            "readyReplicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 1,
            "fullyLabeledReplicas": 1,
            "currentRevision": "current-revision",
            "updateRevision": "current-revision",
            "desiredNumberScheduled": 1,
            "currentNumberScheduled": 1,
            "updatedNumberScheduled": 1,
            "numberReady": 1,
            "numberAvailable": 1,
            "active": 1,
        }
        child_kind = "Job" if kind == "CronJob" else "ReplicaSet"
        child = metadata("owned-child", str(uuid4()), child_kind)
        child["metadata"].update(ownerReferences=owner(kind, "owned-controller", self.uid), generation=1)
        child["spec"] = {"template": copy.deepcopy(self.template), "replicas": 1}
        child["status"] = {"active": 1, "observedGeneration": 1, "replicas": 1, "readyReplicas": 1}
        self.children = [child] if kind in ("Deployment", "CronJob") else []
        if kind == "Deployment":
            child["metadata"]["labels"]["pod-template-hash"] = "current-hash"
            child["spec"]["template"]["metadata"]["labels"]["pod-template-hash"] = "current-hash"
        if kind == "CronJob":
            self.controller["spec"] = {"jobTemplate": {"spec": {"template": copy.deepcopy(self.template)}}}
            self.controller["status"] = {
                "active": [
                    {
                        "apiVersion": "batch/v1",
                        "kind": "Job",
                        "namespace": SUBJECT.namespace,
                        "name": child["metadata"]["name"],
                        "uid": child["metadata"]["uid"],
                    }
                ]
            }
            child["spec"]["template"]["metadata"]["labels"].update(
                {
                    "batch.kubernetes.io/controller-uid": child["metadata"]["uid"],
                    "batch.kubernetes.io/job-name": child["metadata"]["name"],
                }
            )
        self.pod = metadata("owned-pod", str(uuid4()), "Pod")
        self.pod["metadata"]["ownerReferences"] = owner(
            "ReplicaSet" if kind == "Deployment" else "Job" if kind == "CronJob" else kind,
            child["metadata"]["name"] if self.children else "owned-controller",
            child["metadata"]["uid"] if self.children else self.uid,
        )
        self.pod["metadata"]["labels"].update(
            {"controller-revision-hash": "current-revision", "pod-template-hash": "current-hash"}
        )
        self.pod["spec"] = {**copy.deepcopy(self.template["spec"]), "nodeName": "owned-node"}
        self.pod["status"] = {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": "app", "ready": True, "state": {"running": {}}}],
        }
        self.node = metadata("owned-node", str(uuid4()), "Node", namespace=None)
        self.node["metadata"].pop("namespace")
        self.node["metadata"]["labels"].update(
            {
                "cloud.google.com/gke-nodepool": "owned-pool",
                "topology.kubernetes.io/zone": CONTEXT.location,
                "iam.gke.io/gke-metadata-server-enabled": "true",
                "capacity": "2",
            }
        )
        self.node["spec"] = {"providerID": f"gce://{IDENTITY.project_id}/{CONTEXT.location}/owned-node"}
        self.node["status"] = {"conditions": [{"type": "Ready", "status": "True"}]}
        self.get_calls, self.extra = [], []
        self.after_get, self.partial = None, False

    def target(self):
        return WorkloadTarget(
            SUBJECT.namespace,
            self.kind,
            "owned-controller",
            self.uid,
            SUBJECT.name,
            2,
            json.dumps(self.selector),
            template_sha256(self.template),
            placement_sha256(self.template["spec"]),
            1,
            ("owned-pool",),
        )

    def get(self, path):
        self.get_calls.append(path)
        if path.endswith("/pods"):
            value = {
                "apiVersion": "v1",
                "kind": "PodList",
                "metadata": {"resourceVersion": "19", "continue": "foreign" if self.partial else ""},
                "items": [self.pod, *self.extra],
            }
        elif path.endswith(("/replicasets", "/jobs")):
            kind = "Job" if path.endswith("/jobs") else "ReplicaSet"
            value = {
                "apiVersion": module._ROUTES[kind][0],
                "kind": kind + "List",
                "metadata": {"resourceVersion": "19"},
                "items": self.children,
            }
        elif path.startswith("/api/v1/nodes/"):
            value = self.node
        else:
            assert path == (
                f"/apis/{module._ROUTES[self.kind][0]}/namespaces/{SUBJECT.namespace}/"
                f"{module._ROUTES[self.kind][1]}/owned-controller"
            )
            value = self.controller
        result = copy.deepcopy(value)
        if self.after_get:
            self.after_get(path)
        return result


@pytest.fixture
def runtime():
    def make(kind="Deployment"):
        wire, kube = Wire(), RuntimeKube(kind)
        wire.cluster.locations = [CONTEXT.location]
        wire.pools.node_pools[0].locations = [CONTEXT.location]
        return (
            wire,
            kube,
            GKEIdentityRuntimeObserver(
                CONTEXT, (kube.target(),), clients=wire.clients, kubernetes_factory=lambda *a: kube
            ),
        )

    return make


@pytest.mark.parametrize("kind", KINDS)
def test_actual_sdk_all_controller_owned_running_rollouts(runtime, kind):
    wire, kube, observer = runtime(kind)
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed and result.workload_ready, result.reason
    assert result.workloads[0].ready_pod_uids == (kube.pod["metadata"]["uid"],)
    assert result.workloads[0].generation_observed == (kind not in ("Job", "CronJob"))
    assert not result.impersonation_verified and not result.inference_verified
    assert wire.calls == ["GetProject", "GetCluster", "ListNodePools", "ListNodePools"] * 2


@pytest.mark.parametrize("kind", KINDS)
def test_known_pending_not_healthy_empty(runtime, kind):
    _, kube, observer = runtime(kind)
    if kind in ("Deployment", "StatefulSet", "ReplicaSet"):
        kube.controller["status"]["readyReplicas"] = 0
    elif kind == "DaemonSet":
        kube.controller["status"]["numberReady"] = 0
    elif kind == "Job":
        kube.controller["status"].update(active=0, succeeded=1, conditions=[{"type": "Complete", "status": "True"}])
    else:
        kube.controller["status"].update(active=[], lastSuccessfulTime="2026-10-04T00:00:00Z")
        kube.children[0]["status"].update(active=0, succeeded=1, conditions=[{"type": "Complete", "status": "True"}])
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed and not result.workload_ready
    assert result.workloads[0].execution_observed == (kind in ("Job", "CronJob"))


CHANGES = (
    "namespaceUID",
    "ksaUID",
    "annotation",
    "controllerUID",
    "generation",
    "template",
    "selector",
    "hostnetwork",
    "owner",
    "node-project",
    "node-zone",
    "node-pool",
    "metadata-server",
    "node-ready",
    "pod-KSA",
    "pod-args",
    "pod-image",
    "partial",
    "replica-surge",
    "old-RS-owner",
    "current-RS-template",
)


@pytest.mark.parametrize("change", CHANGES)
def test_actual_source_or_rollout_divergence_refuses(runtime, change):
    _, kube, observer = runtime()
    if change == "namespaceUID":
        kube.namespace["metadata"]["uid"] = str(uuid4())
    elif change == "ksaUID":
        kube.account["metadata"]["uid"] = str(uuid4())
    elif change == "annotation":
        kube.account["metadata"]["annotations"]["iam.gke.io/gcp-service-account"] = (
            "foreign@fixture-project.iam.gserviceaccount.com"
        )
    elif change == "controllerUID":
        kube.controller["metadata"]["uid"] = str(uuid4())
    elif change == "generation":
        kube.controller["status"]["observedGeneration"] = 1
    elif change == "template":
        kube.controller["spec"]["template"]["spec"]["containers"][0]["args"] = ["changed"]
    elif change == "selector":
        kube.controller["spec"]["selector"] = {"matchLabels": {"other": "x"}}
    elif change == "hostnetwork":
        kube.pod["spec"]["hostNetwork"] = True
    elif change == "owner":
        kube.pod["metadata"]["ownerReferences"][0]["uid"] = str(uuid4())
    elif change == "node-project":
        kube.node["spec"]["providerID"] = kube.node["spec"]["providerID"].replace(
            IDENTITY.project_id, "foreign-project"
        )
    elif change == "node-zone":
        kube.node["metadata"]["labels"]["topology.kubernetes.io/zone"] = "us-east1-a"
    elif change == "node-pool":
        kube.node["metadata"]["labels"]["cloud.google.com/gke-nodepool"] = "foreign-pool"
    elif change == "metadata-server":
        kube.node["metadata"]["labels"].pop("iam.gke.io/gke-metadata-server-enabled")
    elif change == "node-ready":
        kube.node["status"]["conditions"][0]["status"] = "False"
    elif change == "pod-KSA":
        kube.pod["spec"]["serviceAccountName"] = "foreign"
    elif change == "pod-args":
        kube.pod["spec"]["containers"][0]["args"] = ["changed"]
    elif change == "pod-image":
        kube.pod["spec"]["containers"][0]["image"] = "foreign"
    elif change == "partial":
        kube.partial = True
    elif change == "replica-surge":
        kube.controller["status"]["replicas"] = 2
    elif change == "old-RS-owner":
        kube.children[0]["metadata"]["ownerReferences"][0]["uid"] = str(uuid4())
    else:
        kube.children[0]["spec"]["template"]["spec"]["containers"][0]["args"] = ["old"]
    result = observer.observe(checkpoint=lambda: None)
    assert not result.workload_ready, (change, result)


@pytest.mark.parametrize(
    "op,values,found,success",
    [
        ("In", ["2"], "2", True),
        ("In", ["1"], "2", False),
        ("NotIn", ["1"], "2", True),
        ("NotIn", ["2"], "2", False),
        ("Exists", [], "2", True),
        ("DoesNotExist", [], None, True),
        ("DoesNotExist", [], "2", False),
        ("Gt", ["1"], "2", True),
        ("Gt", ["2"], "2", False),
        ("Lt", ["3"], "2", True),
        ("Lt", ["2"], "2", False),
    ],
)
def test_required_node_affinity_semantics_actual_node(runtime, op, values, found, success):
    wire, kube, _ = runtime("ReplicaSet")
    if found is None:
        kube.node["metadata"]["labels"].pop("capacity")
    else:
        kube.node["metadata"]["labels"]["capacity"] = found
    affinity = {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [{"matchExpressions": [{"key": "capacity", "operator": op, "values": values}]}]
            }
        }
    }
    kube.template["spec"]["affinity"] = affinity
    kube.controller["spec"]["template"] = copy.deepcopy(kube.template)
    kube.pod["spec"]["affinity"] = copy.deepcopy(affinity)
    observer = GKEIdentityRuntimeObserver(
        CONTEXT, (kube.target(),), clients=wire.clients, kubernetes_factory=lambda *a: kube
    )
    assert observer.observe(checkpoint=lambda: None).workload_ready is success


@pytest.mark.parametrize("change", ["controller", "pod", "node", "RS", "ksa", "cluster", "withdraw"])
def test_changed_or_withdrawn_final_observation_not_adopted(runtime, change):
    wire, kube, observer = runtime()
    current = [True]

    def checkpoint():
        if not current[0]:
            raise PermissionError("withdrawn current admission")

    def mutate(path):
        if path.startswith("/api/v1/nodes/") and kube.get_calls.count(path) == 1:
            if change == "controller":
                kube.controller["metadata"]["generation"] = 3
            elif change == "pod":
                kube.pod["status"]["conditions"][0]["status"] = "False"
            elif change == "node":
                kube.node["status"]["conditions"][0]["status"] = "False"
            elif change == "RS":
                kube.children[0]["metadata"]["ownerReferences"][0]["uid"] = str(uuid4())
            elif change == "ksa":
                kube.account["metadata"]["resourceVersion"] = "20"
            elif change == "cluster":
                wire.cluster.endpoint = "34.1.2.4"
            else:
                current[0] = False

    kube.after_get = mutate
    if change == "withdraw":
        with pytest.raises(PermissionError):
            observer.observe(checkpoint=checkpoint)
    else:
        assert not observer.observe(checkpoint=checkpoint).workload_ready


def test_autopilot_ready_no_invented_standard_selector(runtime):
    wire, kube, _ = runtime("ReplicaSet")
    wire.cluster.autopilot.enabled = True
    kube.template["spec"].pop("nodeSelector")
    kube.controller["spec"]["template"] = copy.deepcopy(kube.template)
    kube.pod["spec"].pop("nodeSelector")
    target = replace(kube.target(), allowed_node_pools=())
    observer = GKEIdentityRuntimeObserver(CONTEXT, (target,), clients=wire.clients, kubernetes_factory=lambda *a: kube)
    assert observer.observe(checkpoint=lambda: None).workload_ready
    assert "ListNodePools" not in wire.calls


def test_before_adc_withdrawal_and_read_bound_zero_native_workload(monkeypatch, runtime):
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda **kw: pytest.fail("no ADC"))

    def denied():
        raise PermissionError("withdrawn")

    with pytest.raises(PermissionError):
        GKEIdentityRuntimeObserver(CONTEXT, (RuntimeKube().target(),)).observe(checkpoint=denied)
    _, kube, observer = runtime()
    monkeypatch.setattr(module, "MAX_READS", 0)
    assert not observer.observe(checkpoint=lambda: None).workload_ready
    assert not kube.get_calls


@pytest.fixture
def runtime_tls(request):
    server, state, cert = request.getfixturevalue("tls_server")
    kube = RuntimeKube()
    state.update(raw=None, runtime=kube)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append((self.command, self.path, self.headers.get("Authorization")))
            path = self.path.split("?", 1)[0]
            if path == f"/api/v1/namespaces/{SUBJECT.namespace}":
                value = kube.namespace
            elif path.endswith("/serviceaccounts/" + SUBJECT.name):
                value = kube.account
            else:
                value = kube.get(path)
            raw = state["raw"] if state["raw"] is not None else json.dumps(value).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if state["redirect"]:
                self.send_header("Location", state["redirect"])
            if state["encoding"]:
                self.send_header("Content-Encoding", state["encoding"])
            self.end_headers()
            self.wfile.write(raw)

    server.RequestHandlerClass = Handler
    return server, state, cert, kube


def tls_observer(runtime_tls, monkeypatch, checkpoint):
    server, _, cert, kube = runtime_tls
    monkeypatch.setattr(source, "_endpoint", lambda value: f"https://127.0.0.1:{server.server_port}")
    wire = Wire()
    wire.cluster.locations = [CONTEXT.location]
    wire.pools.node_pools[0].locations = [CONTEXT.location]
    wire.cluster.master_auth.cluster_ca_certificate = cert
    observer = GKEIdentityRuntimeObserver(
        CONTEXT,
        (kube.target(),),
        clients=wire.clients,
        kubernetes_factory=lambda *args: KubernetesRuntimeAdapter(
            args[0], args[1], Credentials(token="runtime-private-token-marker"), args[3]
        ),
    )
    return observer, wire


def test_actual_tls_full_rollout_private_transport(runtime_tls, monkeypatch, caplog):
    _, state, _, kube = runtime_tls
    for template in (kube.template, kube.controller["spec"]["template"], kube.children[0]["spec"]["template"]):
        template["spec"]["containers"][0]["env"] = [{"name": "PRIVATE_TEST", "value": "runtime-private-body-marker"}]
    kube.pod["spec"]["containers"][0]["env"] = [{"name": "PRIVATE_TEST", "value": "runtime-private-body-marker"}]
    caplog.set_level(logging.DEBUG)
    observer, wire = tls_observer(runtime_tls, monkeypatch, lambda: None)
    result = observer.observe(checkpoint=lambda: None)
    assert result.workload_ready, result.reason
    assert any("/pods?limit=257" in item[1] for item in state["requests"])
    assert all(item[0] == "GET" for item in state["requests"])
    assert "runtime-private-token-marker" not in caplog.text and "runtime-private-body-marker" not in caplog.text
    assert wire.calls == ["GetProject", "GetCluster", "ListNodePools", "ListNodePools"] * 2


@pytest.mark.parametrize("failure", ["403", "404", "500", "redirect", "oversized", "gzip", "malformed", "array"])
def test_actual_tls_failed_read_no_body_token_leaks(runtime_tls, monkeypatch, caplog, failure):
    _, state, _, _ = runtime_tls
    if failure.isdigit():
        state["status"] = int(failure)
    elif failure == "redirect":
        state.update(status=302, redirect="https://foreign.invalid/private")
    elif failure == "oversized":
        state["raw"] = b"private-marker" + b"x" * module.MAX_BYTES
    elif failure == "gzip":
        state["encoding"] = "gzip"
    else:
        state["raw"] = b"[]" if failure == "array" else b"private-marker-not-json"
    observer, _ = tls_observer(runtime_tls, monkeypatch, lambda: None)
    result = observer.observe(checkpoint=lambda: None)
    assert not result.workload_ready
    assert len(state["requests"]) == 1
    assert "private-marker" not in caplog.text and "runtime-private-token-marker" not in caplog.text


def test_actual_sdk_native_refusal_zero_kubernetes(runtime, caplog):
    wire, kube, observer = runtime()
    wire.error = PermissionDenied
    result = observer.observe(checkpoint=lambda: None)
    assert not result.configuration_observed and result.reason == "NATIVE_READ_UNCONFIRMED"
    assert not kube.get_calls and not kube.calls
    assert "synthetic-secret-native-error" not in caplog.text


@pytest.mark.parametrize(
    "kind,change",
    [
        ("StatefulSet", "no-revisions"),
        ("StatefulSet", "pod-revision"),
        ("DaemonSet", "old-args"),
        ("DaemonSet", "misscheduled"),
        ("ReplicaSet", "selector"),
        ("Job", "suspended"),
        ("CronJob", "missing-active"),
        ("CronJob", "foreign-active-owner"),
    ],
)
def test_variant_specific_current_rollout_fences(runtime, kind, change):
    _, kube, observer = runtime(kind)
    if change == "no-revisions":
        kube.controller["status"].update(currentRevision=None, updateRevision=None)
    elif change == "pod-revision":
        kube.pod["metadata"]["labels"]["controller-revision-hash"] = "old"
    elif change == "old-args":
        kube.pod["spec"]["containers"][0]["args"] = ["old"]
    elif change == "misscheduled":
        kube.controller["status"]["numberMisscheduled"] = 1
    elif change == "selector":
        kube.controller["spec"]["selector"] = {"matchLabels": {"other": "x"}}
    elif change == "suspended":
        kube.controller["spec"]["suspend"] = True
    elif change == "missing-active":
        kube.children.clear()
    else:
        kube.children[0]["metadata"]["ownerReferences"][0]["uid"] = str(uuid4())
    assert not observer.observe(checkpoint=lambda: None).workload_ready


@pytest.mark.parametrize(
    "change",
    [
        "controller-spec-array",
        "controller-status-array",
        "pod-spec-array",
        "pod-status-array",
        "node-spec-array",
        "node-uid-malformed",
        "duplicate-ready",
        "missing-container-status",
        "container-not-running",
        "duplicate-pod",
        "huge-pod-inventory",
        "foreign-selected-pod",
        "invalid-labels",
        "unknown-affinity",
        "unobserved-zone",
    ],
)
def test_malformed_incomplete_or_ambiguous_metadata_never_ready(runtime, change):
    _, kube, observer = runtime("ReplicaSet")
    if change == "controller-spec-array":
        kube.controller["spec"] = []
    elif change == "controller-status-array":
        kube.controller["status"] = []
    elif change == "pod-spec-array":
        kube.pod["spec"] = []
    elif change == "pod-status-array":
        kube.pod["status"] = []
    elif change == "node-spec-array":
        kube.node["spec"] = []
    elif change == "node-uid-malformed":
        kube.node["metadata"]["uid"] = "private-malformed-uid"
    elif change == "duplicate-ready":
        kube.pod["status"]["conditions"].append({"type": "Ready", "status": "False"})
    elif change == "missing-container-status":
        kube.pod["status"].pop("containerStatuses")
    elif change == "container-not-running":
        kube.pod["status"]["containerStatuses"][0]["state"] = {"waiting": {}}
    elif change == "duplicate-pod":
        kube.extra = [copy.deepcopy(kube.pod)]
    elif change == "huge-pod-inventory":
        kube.extra = [copy.deepcopy(kube.pod)] * module.MAX_ITEMS
    elif change == "foreign-selected-pod":
        kube.pod["metadata"]["ownerReferences"] = owner("ReplicaSet", "foreign", str(uuid4()))
    elif change == "invalid-labels":
        kube.pod["metadata"]["labels"] = []
    elif change == "unknown-affinity":
        kube.pod["spec"]["affinity"] = {"nodeAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": "bad"}}
    else:
        kube.node["spec"]["providerID"] = f"gce://{IDENTITY.project_id}/us-central1-b/owned-node"
        kube.node["metadata"]["labels"]["topology.kubernetes.io/zone"] = "us-central1-b"
    result = observer.observe(checkpoint=lambda: None)
    assert not result.workload_ready, (change, result.reason)
    assert "private-malformed" not in result.reason


def test_native_pool_location_and_current_inventory_changes_refuse(runtime):
    wire, _, observer = runtime()
    wire.pools.node_pools[0].locations = ["us-central1-b"]
    assert observer.observe(checkpoint=lambda: None).reason == "NODE_POOL_LOCATION_UNVERIFIED"
    wire, _, observer = runtime()

    def after(name):
        if name == "ListNodePools" and wire.calls.count(name) == 3:
            wire.pools.node_pools[0].locations = ["us-central1-b"]

    wire.after = after
    assert not observer.observe(checkpoint=lambda: None).workload_ready


def test_preferred_affinity_does_not_force_matching_node_and_required_or_terms(runtime):
    wire, kube, _ = runtime("ReplicaSet")
    affinity = {
        "nodeAffinity": {
            "preferredDuringSchedulingIgnoredDuringExecution": [
                {
                    "weight": 100,
                    "preference": {"matchExpressions": [{"key": "capacity", "operator": "In", "values": ["999"]}]},
                }
            ],
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {"matchExpressions": [{"key": "capacity", "operator": "In", "values": ["999"]}]},
                    {"matchFields": [{"key": "metadata.name", "operator": "In", "values": ["owned-node"]}]},
                ]
            },
        }
    }
    kube.template["spec"]["affinity"] = copy.deepcopy(affinity)
    kube.controller["spec"]["template"] = copy.deepcopy(kube.template)
    kube.pod["spec"]["affinity"] = copy.deepcopy(affinity)
    observer = GKEIdentityRuntimeObserver(
        CONTEXT, (kube.target(),), clients=wire.clients, kubernetes_factory=lambda *a: kube
    )
    assert observer.observe(checkpoint=lambda: None).workload_ready
    kube.pod["spec"]["affinity"]["nodeAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"][0]["weight"] = 1
    assert not observer.observe(checkpoint=lambda: None).workload_ready


def test_known_service_account_defaults_do_not_hide_changed_declared_fields(runtime):
    _, kube, observer = runtime("ReplicaSet")
    kube.pod["spec"]["volumes"] = [
        {
            "name": "kube-api-access-abcde",
            "projected": {
                "sources": [
                    {"serviceAccountToken": {"path": "token", "expirationSeconds": 3607}},
                    {"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}},
                    {
                        "downwardAPI": {
                            "items": [{"path": "namespace", "fieldRef": {"fieldPath": "metadata.namespace"}}]
                        }
                    },
                ]
            },
        }
    ]
    kube.pod["spec"]["containers"][0]["volumeMounts"] = [
        {
            "name": "kube-api-access-abcde",
            "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
            "readOnly": True,
        }
    ]
    kube.pod["spec"]["tolerations"] = [
        {"key": "node.kubernetes.io/not-ready", "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300}
    ]
    assert observer.observe(checkpoint=lambda: None).workload_ready
    kube.pod["spec"]["containers"][0]["args"] = ["unreviewed"]
    assert not observer.observe(checkpoint=lambda: None).workload_ready


def test_actual_tls_held_final_read_withdrawal_cannot_return_rollout(runtime_tls, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _, _, _, kube = runtime_tls
    held, release = Event(), Event()
    current = [True]

    def checkpoint():
        if not current[0]:
            raise PermissionError("current admission withdrawn")

    def hold(path):
        if path.startswith("/api/v1/nodes/") and kube.get_calls.count(path) == 2:
            held.set()
            assert release.wait(5)

    kube.after_get = hold
    observer, _ = tls_observer(runtime_tls, monkeypatch, checkpoint)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(observer.observe, checkpoint=checkpoint)
        try:
            assert held.wait(5)
            current[0] = False
        finally:
            release.set()
        with pytest.raises(PermissionError):
            future.result(timeout=5)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/secrets",
        "/api/v1/namespaces/owned/pods/x/exec",
        "/api/v1/nodes/x?evil=1",
        "/apis/apps/v1/namespaces/owned/deployments/x/status",
        "https://foreign.invalid/api/v1/nodes/x",
    ],
)
def test_runtime_facade_refuses_alternate_route_before_transport(runtime_tls, monkeypatch, path):
    server, state, cert, _ = runtime_tls
    monkeypatch.setattr(source, "_endpoint", lambda value: f"https://127.0.0.1:{server.server_port}")
    adapter = KubernetesRuntimeAdapter(
        "34.1.2.3", cert, Credentials(token="runtime-private-token-marker"), lambda: None
    )
    with pytest.raises(module.GKEObservationError, match="RUNTIME_PATH_REFUSED"):
        adapter.get(path)
    assert state["requests"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("command", ["injected"]),
        ("args", ["injected"]),
        ("env", [{"name": "INJECTED", "value": "bad"}]),
        ("securityContext", {"privileged": True}),
        ("workingDir", "/unreviewed"),
        ("envFrom", [{"secretRef": {"name": "foreign"}}]),
    ],
)
def test_actual_tls_undeclared_execution_is_refused(runtime_tls, monkeypatch, field, value):
    _, _, _, kube = runtime_tls
    # Pin an original controller without this behavior; only observed Pod changes.
    kube.template["spec"]["containers"][0].pop("args")
    kube.controller["spec"]["template"] = copy.deepcopy(kube.template)
    kube.children[0]["spec"]["template"]["spec"]["containers"][0].pop("args")
    kube.pod["spec"]["containers"][0].pop("args")
    observer, _ = tls_observer(runtime_tls, monkeypatch, lambda: None)
    kube.pod["spec"]["containers"][0][field] = value
    result = observer.observe(checkpoint=lambda: None)
    assert not result.workload_ready and result.reason == "POD_TEMPLATE_CHANGED"


def test_actual_tls_bounded_api_defaults_and_bookkeeping_updates(runtime_tls, monkeypatch):
    _, _, _, kube = runtime_tls
    kube.pod["spec"].update(
        dnsPolicy="ClusterFirst",
        restartPolicy="Always",
        securityContext={},
        terminationGracePeriodSeconds=30,
        enableServiceLinks=True,
        hostPID=False,
        hostIPC=False,
    )
    kube.pod["spec"]["containers"][0].update(
        imagePullPolicy="IfNotPresent",
        terminationMessagePolicy="File",
        terminationMessagePath="/dev/termination-log",
        stdin=False,
        tty=False,
        securityContext={},
    )

    def bookkeeping(path):
        if path.startswith("/api/v1/nodes/") and kube.get_calls.count(path) == 1:
            for row in (kube.controller, kube.pod, kube.node, kube.children[0]):
                row["metadata"]["resourceVersion"] = "20"
                row["metadata"]["managedFields"] = [{"manager": "controller"}]
            kube.pod["status"]["conditions"][0]["lastTransitionTime"] = "2026-01-01T00:00:00Z"
            kube.node["status"]["conditions"][0]["lastHeartbeatTime"] = "2026-01-01T00:00:00Z"

    kube.after_get = bookkeeping
    observer, _ = tls_observer(runtime_tls, monkeypatch, lambda: None)
    assert observer.observe(checkpoint=lambda: None).workload_ready


@pytest.mark.parametrize(
    "field,value",
    [
        ("hostPID", True),
        ("hostIPC", True),
        ("securityContext", {"runAsUser": 0}),
        ("ephemeralContainers", [{"name": "unreviewed", "image": "foreign"}]),
        ("automountServiceAccountToken", True),
        ("dnsConfig", {"nameservers": ["1.2.3.4"]}),
    ],
)
def test_actual_tls_undeclared_pod_behavior_is_refused(runtime_tls, monkeypatch, field, value):
    _, _, _, kube = runtime_tls
    observer, _ = tls_observer(runtime_tls, monkeypatch, lambda: None)
    kube.pod["spec"][field] = value
    assert observer.observe(checkpoint=lambda: None).reason == "POD_TEMPLATE_CHANGED"


@pytest.mark.parametrize(
    "change", ["active-name", "inactive-job", "old-active-rs", "toleration", "pod-affinity", "daemon-affinity"]
)
def test_additional_current_execution_and_placement_controls(runtime, change):
    _, kube, observer = runtime(
        "CronJob"
        if change in ("active-name", "inactive-job")
        else "DaemonSet"
        if change == "daemon-affinity"
        else "Deployment"
    )
    if change == "active-name":
        kube.controller["status"]["active"][0]["name"] = "foreign-name"
    elif change == "inactive-job":
        kube.children[0]["status"]["active"] = 0
    elif change == "old-active-rs":
        old = copy.deepcopy(kube.children[0])
        old["metadata"]["uid"] = str(uuid4())
        old["metadata"]["name"] = "old-rs"
        old["spec"]["template"]["spec"]["containers"][0]["image"] = "old"
        kube.children.append(old)
    elif change == "toleration":
        kube.pod["spec"]["tolerations"] = [
            {"key": "node.kubernetes.io/not-ready", "operator": "Exists", "effect": "NoExecute"}
        ]
    elif change == "pod-affinity":
        kube.pod["spec"]["affinity"] = {"podAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": []}}
    else:
        kube.pod["spec"]["affinity"] = {
            "nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": []}}
        }
    assert not observer.observe(checkpoint=lambda: None).workload_ready


def test_daemonset_generated_exact_node_affinity(runtime):
    _, kube, observer = runtime("DaemonSet")
    kube.pod["spec"]["affinity"] = {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {"matchFields": [{"key": "metadata.name", "operator": "In", "values": ["owned-node"]}]}
                ]
            }
        }
    }
    assert observer.observe(checkpoint=lambda: None).workload_ready


@pytest.mark.parametrize(
    "change", ["token-audience", "foreign-cm-item", "foreign-namespace-field", "mount-subpath", "nonboolean-default"]
)
def test_api_defaults_never_admit_undeclared_identity_behavior(runtime, change):
    _, kube, observer = runtime("ReplicaSet")
    if change == "nonboolean-default":
        kube.pod["spec"]["containers"][0]["stdin"] = 0
    else:
        kube.pod["spec"]["volumes"] = [
            {
                "name": "kube-api-access-abcde",
                "projected": {
                    "sources": [
                        {"serviceAccountToken": {"path": "token", "expirationSeconds": 3607}},
                        {"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}},
                        {
                            "downwardAPI": {
                                "items": [{"path": "namespace", "fieldRef": {"fieldPath": "metadata.namespace"}}]
                            }
                        },
                    ]
                },
            }
        ]
        kube.pod["spec"]["containers"][0]["volumeMounts"] = [
            {
                "name": "kube-api-access-abcde",
                "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
                "readOnly": True,
            }
        ]
        sources = kube.pod["spec"]["volumes"][0]["projected"]["sources"]
        if change == "token-audience":
            sources[0]["serviceAccountToken"]["audience"] = "foreign"
        elif change == "foreign-cm-item":
            sources[1]["configMap"]["items"][0]["key"] = "foreign"
        elif change == "foreign-namespace-field":
            sources[2]["downwardAPI"]["items"][0]["fieldRef"]["fieldPath"] = "metadata.name"
        else:
            kube.pod["spec"]["containers"][0]["volumeMounts"][0]["subPath"] = "foreign"
    assert observer.observe(checkpoint=lambda: None).reason == "POD_TEMPLATE_CHANGED"


@pytest.mark.parametrize("kind", ["Job", "CronJob"])
def test_completion_count_or_cron_clock_alone_never_proves_execution(runtime, kind):
    _, kube, observer = runtime(kind)
    if kind == "Job":
        kube.controller["status"].update(active=0, succeeded=1)
    else:
        kube.controller["status"].update(active=[], lastSuccessfulTime="2026-01-01T00:00:00Z")
        kube.children[0]["status"].update(active=0, succeeded=1)
    result = observer.observe(checkpoint=lambda: None)
    assert not result.workload_ready and not result.workloads[0].execution_observed


@pytest.mark.parametrize("change", ["pod-hash", "rs-hash", "rs-observed-generation"])
def test_deployment_current_replica_set_revision_required(runtime, change):
    _, kube, observer = runtime()
    if change == "pod-hash":
        kube.pod["metadata"]["labels"]["pod-template-hash"] = "old"
    elif change == "rs-hash":
        kube.children[0]["metadata"]["labels"].pop("pod-template-hash")
    else:
        kube.children[0]["status"]["observedGeneration"] = 0
    assert not observer.observe(checkpoint=lambda: None).workload_ready
