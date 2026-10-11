"""The control plane's minimal RBAC contract holds (calliope-installer#447).

``deploy/rbac/control-plane-minimal.yaml`` is what the installer binds the
control plane to when it withholds cluster-wide changes. This file fails
when backend code starts touching a Kubernetes resource the contract does
not grant, unless that code is on a path that refuses up front
(``BEYOND_MINIMAL``); and it proves the transport guard refuses everything
else with the reason instead of letting the apiserver answer 403.

The scan reads every non-test module under ``backend/`` for:

* manifest literals: dicts with ``apiVersion`` and ``kind`` (module
  constants, f-strings of them and ``a if c else b`` resolved; a ``kind``
  held in a variable is paired with the module's ``*_KIND`` constants),
  ``_manifest("group/v", "Kind")`` style helpers, ``{"Kind": API_VERSION}``
  lookup tables, and ``"group/version/Kind"`` strings;
* the rules of every Role the code renders, which the apiserver's
  escalation check holds to what the contract grants;
* kinds passed to the driver and dynamic-client reads and deletes
  (``kind=`` keywords, and ``get_manifest(slug, ns, "Kind", name)``);
* typed kubernetes-client methods (``CoreV1Api.list_namespaced_pod`` …);
* literal ``call_api("/path", "METHOD")`` requests.

A kind built at run time from a variable is not visible to the scan; the
transport guard still refuses it at run time with the reason.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core import control_plane_rbac as rbac
from core import install_restrictions as ir

BACKEND = Path(__file__).resolve().parents[2]
EKS = "https://0123456789ABCDEF.gr7.us-east-1.eks.amazonaws.com"

# ---- what the scan needs to know about kinds ---------------------------------

#: Bare kinds the code passes without a group.
BARE_KIND_GROUPS = {
    "Pod": "",
    "Service": "",
    "ConfigMap": "",
    "Secret": "",
    "Namespace": "",
    "ServiceAccount": "",
    "PersistentVolumeClaim": "",
    "PersistentVolume": "",
    "Node": "",
    "Deployment": "apps",
    "StatefulSet": "apps",
    "Job": "batch",
    "CronJob": "batch",
    "Ingress": "networking.k8s.io",
    "NetworkPolicy": "networking.k8s.io",
    "StorageClass": "storage.k8s.io",
    "RuntimeClass": "node.k8s.io",
    "CustomResourceDefinition": "apiextensions.k8s.io",
    "Certificate": "cert-manager.io",
}

#: Group for the few kinds whose apiVersion is chosen at run time.
RUNTIME_GROUPS = {
    "Cluster": ("postgresql.cnpg.io",),
    "OpenSearchCluster": ("opensearch.opster.io", "opensearch.org"),
    "OpensearchRole": ("opensearch.opster.io", "opensearch.org"),
    "OpensearchUser": ("opensearch.opster.io", "opensearch.org"),
    "OpensearchUserRoleBinding": ("opensearch.opster.io", "opensearch.org"),
}

#: Plurals the regular rules below get wrong.
PLURALS = {"Redis": "redis", "S3Credentials": "s3credentials", "Prometheus": "prometheuses"}

#: Kinds the code only reads. Each still needs get and list.
READ_ONLY = {
    "CustomResourceDefinition",
    "CSIDriver",
    "Node",
    "RuntimeClass",
    "Prometheus",
    "ReplicaSet",
    "StorageClass",
    "VolumeSnapshotClass",
}

#: Modules whose ``{"Kind": apiVersion}`` tables describe every kind a
#: generic client can address, not kinds the code uses.
GENERIC_KIND_TABLES = {"providers/_sdk/k8s_dynamic_client.py"}

#: Dicts shaped like manifests that are not API objects.
NOT_API_OBJECTS = {"Config", "DeleteOptions"}

#: Typed kubernetes-client methods the code calls, in RBAC terms.
TYPED_METHODS = {
    "list_namespaced_pod": ("list", "", "pods"),
    "read_namespaced_pod_log": ("get", "", "pods/log"),
    "connect_get_namespaced_pod_exec": ("create", "", "pods/exec"),
    "connect_get_namespaced_pod_portforward": ("create", "", "pods/portforward"),
    "list_namespaced_event": ("list", "", "events"),
    "list_namespaced_deployment": ("list", "apps", "deployments"),
    "read_namespaced_deployment": ("get", "apps", "deployments"),
    "read_namespaced_replica_set": ("get", "apps", "replicasets"),
    "read_namespaced_stateful_set": ("get", "apps", "statefulsets"),
    "read_namespaced_daemon_set": ("get", "apps", "daemonsets"),
    "read_namespaced_job": ("get", "batch", "jobs"),
    "read_namespaced_cron_job": ("get", "batch", "cronjobs"),
    "list_namespaced_ingress": ("list", "networking.k8s.io", "ingresses"),
    "create_namespaced_job": ("create", "batch", "jobs"),
    "read_namespaced_job_status": ("get", "batch", "jobs/status"),
    "list_node": ("list", "", "nodes"),
    "list_storage_class": ("list", "storage.k8s.io", "storageclasses"),
    "list_custom_resource_definition": ("list", "apiextensions.k8s.io", "customresourcedefinitions"),
    # Driver methods share these names with the typed client; both mean the same request.
    "create_namespace": ("create", "", "namespaces"),
    "delete_namespace": ("delete", "", "namespaces"),
}

APPLY_VERBS = ("get", "list", "create", "patch", "delete")
KIND_ARG_CALLS = {"get_manifest", "list_manifests", "get_workload_status", "patch_workload", "poll_rollout"}


def plural(kind: str) -> str:
    lower = kind.lower()
    if kind in PLURALS:
        return PLURALS[kind]
    if lower.endswith("y") and lower[-2:-1] not in "aeiou":
        return lower[:-1] + "ies"
    if lower.endswith(("s", "x")):
        return lower + "es"
    return lower + "s"


def _api_methods() -> set[str]:
    import kubernetes.client as kc

    names: set[str] = set()
    for name, obj in vars(kc).items():
        if name.endswith("Api") and inspect.isclass(obj):
            names |= {m for m in vars(obj) if not m.startswith("_") and not m.endswith("_with_http_info")}
    return names


def _module_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.update({t.id: value.value for t in targets if isinstance(t, ast.Name)})
    return out


def _strings(node: ast.AST | None, consts: dict[str, str]) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and node.id in consts:
        return [consts[node.id]]
    if isinstance(node, ast.IfExp):
        return _strings(node.body, consts) + _strings(node.orelse, consts)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            inner = value.value if isinstance(value, ast.FormattedValue) else value
            resolved = _strings(inner, consts)
            if len(resolved) != 1:
                return []
            parts.append(resolved[0])
        return ["".join(parts)]
    return []


_VERSION = re.compile(r"v\d+((alpha|beta)\d+)?")


def _is_kind(value: str) -> bool:
    return value[:1].isupper() and value.isidentifier()


def _str_list(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    values = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return values if len(values) == len(node.elts) else None


def _is_api_version(value: str) -> bool:
    return value == "v1" or ("/" in value and value.rsplit("/", 1)[-1].startswith("v"))


def _group(api_version: str) -> str:
    return api_version.rsplit("/", 1)[0] if "/" in api_version else ""


def _split_qualified(kind: str) -> tuple[str | None, str]:
    """``group/version/Kind`` or ``v1/Kind`` → (group, Kind); bare → (None, Kind)."""
    parts = kind.split("/")
    if len(parts) == 3:
        return parts[0], parts[2]
    if len(parts) == 2:
        return "", parts[1]
    return None, kind


def _sources():
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if {"tests", "testing", "migrations", "node_modules", ".venv"} & set(rel.parts):
            continue
        yield rel.as_posix(), ast.parse(path.read_text())


def scan() -> SimpleNamespace:
    """Every Kubernetes resource use the scan finds."""
    api_methods = _api_methods()
    kinds: list[tuple[tuple[str, ...], str, str]] = []
    typed: list[tuple[str, str]] = []
    requests: list[tuple[str, str, str]] = []
    unresolved: list[str] = []
    role_rules: list[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []

    for rel, tree in _sources():
        consts = _module_constants(tree)
        kind_consts = [v for k, v in consts.items() if k.endswith("KIND") and _is_kind(v)]
        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Dict):
                fields = {
                    k.value: v
                    for k, v in zip(node.keys, node.values, strict=False)
                    if isinstance(k, ast.Constant)
                }
                if "apiVersion" in fields and "kind" in fields:
                    names = _strings(fields["kind"], consts)
                    versions = _strings(fields["apiVersion"], consts)
                    if not names and versions and isinstance(fields["kind"], ast.Name):
                        # ``"kind": kind`` from a loop over the module's kinds.
                        names = kind_consts
                    for kind in names:
                        if versions:
                            kinds.extend(((_group(v),), kind, where) for v in versions)
                        elif kind in RUNTIME_GROUPS:
                            kinds.append((RUNTIME_GROUPS[kind], kind, where))
                        elif kind not in NOT_API_OBJECTS:
                            unresolved.append(f"{kind} at {where}")
                elif (
                    fields
                    and rel not in GENERIC_KIND_TABLES
                    and all(isinstance(k, str) and _is_kind(k) for k in fields)
                ):
                    # ``{"HTTPRoute": API_VERSION, ...}``: a table of the kinds a driver renders.
                    for kind, value in fields.items():
                        versions = [
                            v for v in _strings(value, consts) if _VERSION.fullmatch(v.rsplit("/", 1)[-1])
                        ]
                        kinds.extend(((_group(v),), kind, where) for v in versions)
                groups, resources, verbs = (
                    _str_list(fields.get(f)) for f in ("apiGroups", "resources", "verbs")
                )
                if groups is not None and resources is not None and verbs is not None:
                    role_rules.append((where, tuple(groups), tuple(resources), tuple(verbs)))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                parts = node.value.split("/")
                if (
                    len(parts) == 3
                    and "." in parts[0]
                    and parts[1].startswith("v")
                    and parts[2][:1].isupper()
                    and parts[2].isidentifier()
                ):
                    kinds.append(((parts[0],), parts[2], where))
            elif isinstance(node, ast.Attribute) and node.attr in api_methods:
                typed.append((node.attr, where))
            if not isinstance(node, ast.Call):
                continue
            func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if len(node.args) >= 2:
                first, second = _strings(node.args[0], consts), _strings(node.args[1], consts)
                if func == "call_api" and first and second:
                    requests.append((second[0], first[0], where))
                elif (
                    first
                    and second
                    and _is_api_version(first[0])
                    and second[0][:1].isupper()
                    and second[0].isidentifier()
                ):
                    kinds.append(((_group(first[0]),), second[0], where))
            candidates = [k.value for k in node.keywords if k.arg == "kind"]
            if func in KIND_ARG_CALLS and len(node.args) >= 3:
                candidates.append(node.args[2])
            for candidate in candidates:
                for value in _strings(candidate, consts):
                    group, kind = _split_qualified(value)
                    if group is None:
                        if kind not in BARE_KIND_GROUPS:
                            continue  # domain kinds ("user", "APP", workflow names), not Kubernetes
                        group = BARE_KIND_GROUPS[kind]
                    kinds.append(((group,), kind, where))
    return SimpleNamespace(
        kinds=kinds, typed=typed, requests=requests, unresolved=unresolved, role_rules=role_rules
    )


@pytest.fixture(scope="module")
def usage():
    return scan()


# ---- the manifest itself ------------------------------------------------------


def _docs():
    return [d for d in yaml.safe_load_all(rbac.MANIFEST_PATH.read_text()) if d]


def test_the_manifest_is_cluster_roles_bound_to_the_control_plane_group():
    docs = _docs()
    assert {d["kind"] for d in docs} == {
        "ClusterRole",
        "ClusterRoleBinding",
        "ValidatingAdmissionPolicy",
        "ValidatingAdmissionPolicyBinding",
    }
    roles = {d["metadata"]["name"] for d in docs if d["kind"] == "ClusterRole"}
    bindings = [d for d in docs if d["kind"] == "ClusterRoleBinding"]
    assert {b["roleRef"]["name"] for b in bindings} == roles
    for binding in bindings:
        assert binding["subjects"] == [
            {"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": rbac.GROUP}
        ]


def test_the_manifest_grants_nothing_that_is_cluster_admin_by_another_name():
    """No wildcard, no RBAC escalation verbs, and no write on the objects the
    minimal contract leaves to the cluster's owner."""
    owner_only = {
        ("rbac.authorization.k8s.io", "clusterroles"),
        ("rbac.authorization.k8s.io", "clusterrolebindings"),
        ("apiextensions.k8s.io", "customresourcedefinitions"),
        ("admissionregistration.k8s.io", "mutatingwebhookconfigurations"),
        ("admissionregistration.k8s.io", "validatingwebhookconfigurations"),
        ("storage.k8s.io", "storageclasses"),
        ("snapshot.storage.k8s.io", "volumesnapshotclasses"),
        ("node.k8s.io", "runtimeclasses"),
        ("", "persistentvolumes"),
        ("", "nodes"),
        ("apps", "daemonsets"),
        ("helm.toolkit.fluxcd.io", "helmreleases"),
        ("source.toolkit.fluxcd.io", "helmrepositories"),
        ("gateway.networking.k8s.io", "gatewayclasses"),
        ("certificates.k8s.io", "certificatesigningrequests"),
    }
    writes = {"create", "update", "patch", "delete", "deletecollection"}
    for rule in rbac._rules():
        for field in ("apiGroups", "resources", "verbs"):
            assert "*" not in rule[field], rule
        assert not {"escalate", "bind", "impersonate"} & set(rule["verbs"]), rule
        assert "nonResourceURLs" not in rule, rule
        if writes & set(rule["verbs"]):
            for group in rule["apiGroups"]:
                for resource in rule["resources"]:
                    assert (group, resource) not in owner_only, (group, resource)


def test_beyond_minimal_kinds_are_not_granted():
    groups = {
        "ClusterRole": "rbac.authorization.k8s.io",
        "ClusterRoleBinding": "rbac.authorization.k8s.io",
        "HelmRelease": "helm.toolkit.fluxcd.io",
        "HelmRepository": "source.toolkit.fluxcd.io",
        "StorageClass": "storage.k8s.io",
        "VolumeSnapshotClass": "snapshot.storage.k8s.io",
        "PersistentVolume": "",
        "KnativeServing": "operator.knative.dev",
        "GatewayClass": "gateway.networking.k8s.io",
        "EnvoyProxy": "gateway.envoyproxy.io",
        "ClientTrafficPolicy": "gateway.envoyproxy.io",
    }
    assert set(groups) == set(rbac.BEYOND_MINIMAL)
    for kind, group in groups.items():
        assert not rbac.allows("create", group, plural(kind)), kind


def _policies() -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in _docs() if d["kind"] == "ValidatingAdmissionPolicy"}


def test_admission_confines_the_group_to_labelled_namespaces():
    """RBAC binds the namespaced grants cluster-wide; admission keeps the
    group's writes, and exec, out of every namespace it did not create.
    The CEL itself was exercised against a kind apiserver (1.37): see the PR."""
    docs = _docs()
    bindings = {d["spec"]["policyName"]: d for d in docs if d["kind"] == "ValidatingAdmissionPolicyBinding"}
    policies = _policies()
    assert (
        set(bindings)
        == set(policies)
        == {
            "astrolift-control-plane-scope",
            "astrolift-control-plane-pod-security",
        }
    )
    for name, policy in policies.items():
        assert policy["spec"]["failurePolicy"] == "Fail", name
        assert bindings[name]["spec"]["validationActions"] == ["Deny"], name

    scope = policies["astrolift-control-plane-scope"]["spec"]
    assert scope["matchConditions"] == [
        {"name": "control-plane", "expression": f"'{rbac.GROUP}' in request.userInfo.groups"}
    ]
    namespaced, namespaces = scope["matchConstraints"]["resourceRules"]
    assert namespaced["resources"] == ["*/*"] and namespaced["apiGroups"] == ["*"]
    assert set(namespaced["operations"]) == {"CREATE", "UPDATE", "DELETE", "CONNECT"}
    assert namespaces["resources"] == ["namespaces"]
    assert set(namespaces["operations"]) == {"CREATE", "UPDATE", "DELETE"}
    text = yaml.safe_dump(scope)
    assert text.count(rbac.SCOPE_LABEL) >= 3

    pods = policies["astrolift-control-plane-pod-security"]["spec"]
    assert "matchConditions" not in pods, "controllers create the pods; every creator is checked"
    assert pods["matchConstraints"]["namespaceSelector"] == {"matchLabels": {rbac.SCOPE_LABEL: "true"}}
    rules = yaml.safe_dump(pods["validations"])
    for field in ("hostNetwork", "hostPID", "hostIPC", "hostPath", "privileged", "capabilities"):
        assert field in rules, field


def test_the_argo_executor_role_and_the_gateway_kinds_are_granted():
    """The kinds the review found missing (#2324): each is now granted."""
    for resource in ("workflows", "cronworkflows", "workfloweventbindings", "workflowtemplates"):
        for verb in APPLY_VERBS:
            assert rbac.allows(verb, "argoproj.io", resource), (verb, resource)
    for verb in ("create", "patch"):
        assert rbac.allows(verb, "argoproj.io", "workflowtaskresults")
    for resource in (
        "grpcroutes",
        "tlsroutes",
        "tcproutes",
        "udproutes",
        "backendtlspolicies",
        "listenersets",
    ):
        for verb in APPLY_VERBS:
            assert rbac.allows(verb, "gateway.networking.k8s.io", resource), (verb, resource)


# ---- the code against the manifest -------------------------------------------


def test_the_scan_sees_the_code(usage):
    """Guard the scan itself: if it stops finding these, it is broken, not the code."""
    seen = {kind for _, kind, _ in usage.kinds}
    assert {"Deployment", "Secret", "Namespace", "Job", "Ingress", "HelmRelease", "ClusterRole"} <= seen
    assert {"list_namespaced_pod", "connect_get_namespaced_pod_exec"} <= {name for name, _ in usage.typed}
    assert usage.requests
    # Kinds held in f-strings, lookup tables and loop variables.
    assert {"Workflow", "CronWorkflow", "WorkflowEventBinding", "GRPCRoute", "ListenerSet"} <= seen
    assert {where.split(":")[0] for where, *_ in usage.role_rules} >= {
        "providers/k8s_native/managed/workflow_argo.py",
        "providers/k8s_native/managed/model_endpoint_vllm.py",
    }


def test_every_role_the_code_renders_grants_only_what_the_contract_holds(usage):
    """The apiserver refuses a Role that grants more than its creator holds."""
    cluster_roles = set(rbac.BEYOND_MINIMAL["ClusterRole"])
    missing = [
        f"{verb} {resource}.{group or 'core'} ({where})"
        for where, groups, resources, verbs in usage.role_rules
        if where.rsplit(":", 1)[0] not in cluster_roles
        for group in groups
        for resource in resources
        for verb in verbs
        if not rbac.allows(verb, group, resource)
    ]
    assert (
        missing == []
    ), "a Role the code renders grants what deploy/rbac/control-plane-minimal.yaml does not"


def test_every_manifest_the_code_renders_has_a_known_group(usage):
    assert (
        usage.unresolved == []
    ), "a manifest kind whose apiVersion the scan cannot resolve; add it to RUNTIME_GROUPS"


def test_every_kind_the_code_uses_is_granted_or_refused_up_front(usage):
    missing: list[str] = []
    misplaced: list[str] = []
    for groups, kind, where in usage.kinds:
        if kind in NOT_API_OBJECTS:
            continue
        rel = where.rsplit(":", 1)[0]
        if kind in rbac.BEYOND_MINIMAL:
            if rel not in rbac.BEYOND_MINIMAL[kind] and kind not in READ_ONLY:
                misplaced.append(f"{kind} at {where}")
            continue
        verbs = ("get", "list") if kind in READ_ONLY else APPLY_VERBS
        for group in groups:
            for verb in verbs:
                if not rbac.allows(verb, group, plural(kind)):
                    missing.append(f"{verb} {plural(kind)}.{group or 'core'} ({kind} at {where})")
    assert misplaced == [], (
        "code renders a resource the minimal contract leaves to the cluster's owner outside the "
        "paths that refuse up front; refuse it with cluster_scope_refusal and list the file in "
        "core.control_plane_rbac.BEYOND_MINIMAL"
    )
    assert missing == [], (
        "code uses a resource deploy/rbac/control-plane-minimal.yaml does not grant; grant it there "
        "or refuse the path up front and list it in BEYOND_MINIMAL"
    )


def test_every_typed_client_call_is_granted(usage):
    unknown = sorted({f"{name} at {where}" for name, where in usage.typed if name not in TYPED_METHODS})
    assert (
        unknown == []
    ), "a kubernetes-client method the contract has not classified; add it to TYPED_METHODS"
    for name, where in usage.typed:
        verb, group, resource = TYPED_METHODS[name]
        assert rbac.allows(verb, group, resource), f"{name} at {where}: {verb} {resource}.{group or 'core'}"


def test_every_literal_request_is_granted_or_discovery(usage):
    for method, path, where in usage.requests:
        request = rbac.classify(method, path)
        if request is not None:
            assert rbac.allows(
                request.verb, request.group, request.resource
            ), f"{where}: {request.describe()}"


# ---- reading requests ---------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "query", "expected"),
    [
        ("GET", "/api/v1/namespaces/a/pods", None, ("list", "", "pods", "a", "")),
        ("GET", "/api/v1/namespaces/a/pods", {"watch": "true"}, ("watch", "", "pods", "a", "")),
        ("GET", "/api/v1/namespaces/a/pods/p/log", None, ("get", "", "pods/log", "a", "p")),
        ("GET", "/api/v1/namespaces/a/pods/p/exec", None, ("create", "", "pods/exec", "a", "p")),
        ("POST", "/api/v1/namespaces", None, ("create", "", "namespaces", "", "")),
        ("DELETE", "/api/v1/namespaces/a", None, ("delete", "", "namespaces", "", "a")),
        ("GET", "/api/v1/nodes", None, ("list", "", "nodes", "", "")),
        (
            "PATCH",
            "/apis/apps/v1/namespaces/a/deployments/d",
            None,
            ("patch", "apps", "deployments", "a", "d"),
        ),
        ("GET", "/apis/batch/v1/namespaces/a/jobs/j/status", None, ("get", "batch", "jobs/status", "a", "j")),
        (
            "POST",
            "/apis/rbac.authorization.k8s.io/v1/clusterroles",
            None,
            ("create", "rbac.authorization.k8s.io", "clusterroles", "", ""),
        ),
        (
            "DELETE",
            "/apis/apps/v1/namespaces/a/deployments",
            None,
            ("deletecollection", "apps", "deployments", "a", ""),
        ),
    ],
)
def test_requests_read_as_rbac(method, path, query, expected):
    request = rbac.classify(method, path, query)
    assert (request.verb, request.group, request.resource, request.namespace, request.name) == expected


@pytest.mark.parametrize("path", ["/api", "/apis", "/api/v1", "/apis/apps/v1", "/version", "/openapi/v2"])
def test_discovery_is_not_a_resource_request(path):
    assert rbac.classify("GET", path) is None


# ---- the transport guard ------------------------------------------------------


class _Response:
    status = 200
    reason = "OK"
    data = b'{"kind": "List", "items": []}'

    def getheaders(self):
        return {}

    def getheader(self, name, default=None):
        return default


class _Seen(list):
    """(method, url) of each request that reached the fake apiserver, and its body."""

    def __init__(self):
        super().__init__()
        self.bodies: list = []


@pytest.fixture
def apiserver(monkeypatch):
    """A fake apiserver behind the real kubernetes client: records every request that reaches it."""
    from kubernetes import client

    assert rbac.install_transport_guard()
    seen = _Seen()
    bodies = seen.bodies

    def request(self, method, url, *args, **kwargs):
        seen.append((method, url))
        bodies.append(kwargs.get("body"))
        return _Response()

    monkeypatch.setattr(client.ApiClient, "request", request)
    monkeypatch.delenv(ir.ENV_VAR, raising=False)
    return seen


def _api(host=EKS):
    from kubernetes import client

    cfg = client.Configuration()
    cfg.host = host
    return client.ApiClient(configuration=cfg)


def _cluster_role():
    return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "x"}}


def test_unset_sends_everything_as_before(apiserver):
    from kubernetes import client

    client.RbacAuthorizationV1Api(_api()).create_cluster_role(body=_cluster_role(), _preload_content=False)
    assert [m for m, _ in apiserver] == ["POST"]


def test_withheld_refuses_a_cluster_role_with_the_reason_before_it_is_sent(apiserver, monkeypatch):
    from kubernetes import client

    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    with pytest.raises(ir.WithheldCapabilityError, match="Cluster-wide changes are withheld") as caught:
        client.RbacAuthorizationV1Api(_api()).create_cluster_role(body=_cluster_role())
    assert "create clusterroles.rbac.authorization.k8s.io (cluster-wide)" in str(caught.value)
    assert caught.value.non_retryable
    assert apiserver == []


def test_withheld_sends_what_the_contract_grants(apiserver, monkeypatch):
    from kubernetes import client

    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    client.CoreV1Api(_api()).list_namespaced_pod("app-ns", _preload_content=False)
    client.CoreV1Api(_api()).create_namespace(
        body={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "n"}}, _preload_content=False
    )
    assert [m for m, _ in apiserver] == ["GET", "POST"]


def _namespace(**labels):
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "n", "labels": labels}}


def test_withheld_labels_every_namespace_the_control_plane_writes(apiserver, monkeypatch):
    """The admission policy lets the group write only in labelled namespaces;
    the guard labels each namespace it creates or applies, keeping its labels."""
    from kubernetes import client

    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    core = client.CoreV1Api(_api())
    core.create_namespace(body=_namespace(team="a"), _preload_content=False)
    core.create_namespace(
        body=client.V1Namespace(metadata=client.V1ObjectMeta(name="m")), _preload_content=False
    )
    _api().call_api(
        "/api/v1/namespaces/{name}",
        "PATCH",
        {"name": "n"},
        [("fieldManager", "astrolift")],
        {"Content-Type": "application/apply-patch+yaml"},
        body=_namespace(),
        _preload_content=False,
    )
    core.create_namespaced_config_map("n", body={"metadata": {"name": "c"}}, _preload_content=False)
    first, model, applied, config_map = apiserver.bodies
    assert first["metadata"]["labels"] == {"team": "a", rbac.SCOPE_LABEL: "true"}
    assert model["metadata"] == {"name": "m", "labels": {rbac.SCOPE_LABEL: "true"}}
    assert applied["metadata"]["labels"] == {rbac.SCOPE_LABEL: "true"}
    assert config_map == {"metadata": {"name": "c"}}


@pytest.mark.parametrize(
    ("withheld", "host"), [("", EKS), ("controllers", "https://k8s.onprem.example:6443")]
)
def test_namespaces_are_not_labelled_outside_the_contract(apiserver, monkeypatch, withheld, host):
    from kubernetes import client

    monkeypatch.setenv(ir.ENV_VAR, withheld)
    client.CoreV1Api(_api(host)).create_namespace(body=_namespace(), _preload_content=False)
    assert apiserver.bodies == [_namespace()]


def test_withheld_refuses_through_the_dynamic_client(apiserver, monkeypatch):
    """The dynamic client the drivers apply with goes through the same guard."""
    from kubernetes import client

    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    api = _api()
    with pytest.raises(ir.WithheldCapabilityError, match="persistentvolumes"):
        api.call_api("/api/v1/persistentvolumes", "POST", {}, [], {}, body={}, _preload_content=False)
    with pytest.raises(ir.WithheldCapabilityError, match="helmreleases.helm.toolkit.fluxcd.io"):
        api.call_api(
            "/apis/helm.toolkit.fluxcd.io/v2/namespaces/astrolift-system/helmreleases/cert-manager",
            "PATCH",
            {},
            [],
            {},
            body={},
            _preload_content=False,
        )
    client.ApiClient.call_api(api, "/apis", "GET", {}, [], {}, _preload_content=False)
    assert [u for _, u in apiserver] == [f"{EKS}/apis"]


def test_a_cluster_off_eks_is_not_held_to_the_contract(apiserver, monkeypatch):
    """The installer binds the contract on the EKS clusters it hands over; an
    on-prem, GKE or AKS cluster keeps the access its owner registered."""
    from kubernetes import client

    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    client.RbacAuthorizationV1Api(_api("https://k8s.onprem.example:6443")).create_cluster_role(
        body=_cluster_role(), _preload_content=False
    )
    assert len(apiserver) == 1


def test_other_withheld_capabilities_do_not_engage_the_contract(apiserver, monkeypatch):
    from kubernetes import client

    monkeypatch.setenv(ir.ENV_VAR, "dns,databases,load_balancers,clusters")
    client.RbacAuthorizationV1Api(_api()).create_cluster_role(body=_cluster_role(), _preload_content=False)
    assert len(apiserver) == 1


def test_the_guard_is_installed_once():
    from kubernetes.client import api_client

    assert rbac.install_transport_guard()
    first = api_client.ApiClient.call_api
    assert rbac.install_transport_guard()
    assert api_client.ApiClient.call_api is first
    assert getattr(first, "_astrolift_rbac_guard", False)
