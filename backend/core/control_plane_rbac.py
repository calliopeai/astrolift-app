"""The control plane's minimal Kubernetes RBAC contract (calliope-installer#447).

``deploy/rbac/control-plane-minimal.yaml`` is what the control plane may do
on a tenant cluster when the install withholds cluster-wide changes
(``controllers`` in ``ASTROLIFT_WITHHELD_CAPABILITIES``). The installer
applies it and binds the control plane's EKS access entry to the
``astrolift:control-plane`` group in place of cluster admin.

Two things here hold the code to it:

* :func:`install_transport_guard` checks every request the kubernetes client
  sends to an EKS apiserver against the file, and refuses one it does not
  grant with the restriction reason before it leaves the process. The
  apiserver would answer 403; this way a workflow learns why, and Temporal
  does not retry it. It also labels every namespace the control plane
  writes with :data:`SCOPE_LABEL`, which the file's admission policy
  confines the control plane's writes to.
* ``core/tests/test_control_plane_rbac_contract.py`` scans backend code for
  every resource it touches and fails when one is not granted here and is
  not on a path that refuses up front (:data:`BEYOND_MINIMAL`).

Unset, or without ``controllers``, nothing here acts and behaviour is
exactly as before.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "deploy" / "rbac" / "control-plane-minimal.yaml"

#: The group the installer maps the control plane's access entry to.
GROUP = "astrolift:control-plane"

#: The namespace label the file's admission policy confines the group's
#: writes to. The guard adds it to every namespace the control plane writes,
#: so the namespaces it creates are in scope and no other namespace is.
SCOPE_LABEL = "astrolift.io/control-plane-scope"

#: Resources the minimal contract leaves to the cluster's owner, keyed by
#: kind, with the backend files allowed to render them. Each of those paths
#: refuses up front when the install withholds ``controllers``:
#:
#: - bootstrap recipe (``install_prereqs``, the EKS recipe, the Envoy edge,
#:   CSI classes): every component is withheld and the install refuses;
#: - platform RBAC at registration: skipped, the installer's bindings stand in;
#: - keep-alive agent and CloudWatch collector: refused at review and request;
#: - CSI filesystem bindings: refused in the binding preflight.
BEYOND_MINIMAL: dict[str, tuple[str, ...]] = {
    "ClusterRole": ("core/cluster_management.py", "providers/k8s_native/management.py"),
    "ClusterRoleBinding": ("core/cluster_management.py", "providers/k8s_native/management.py"),
    "HelmRelease": ("astrolift_workflows/activities/install_prereqs.py",),
    "HelmRepository": ("astrolift_workflows/activities/install_prereqs.py",),
    "StorageClass": ("providers/_sdk/csi.py",),
    "VolumeSnapshotClass": ("providers/_sdk/csi.py",),
    "PersistentVolume": ("astrolift_services/filesystem_bindings.py",),
    "KnativeServing": ("providers/aws/cluster_eks.py",),
    "GatewayClass": ("providers/k8s_native/edge_gateway.py",),
    "EnvoyProxy": ("providers/k8s_native/edge_gateway.py",),
    "ClientTrafficPolicy": ("providers/k8s_native/edge_gateway.py",),
}

_EKS_HOST = re.compile(r"\.eks\.amazonaws\.com(\.cn)?(:\d+)?/?$", re.IGNORECASE)


@dataclass(frozen=True)
class ApiRequest:
    """One Kubernetes API request, in RBAC terms."""

    verb: str
    group: str
    resource: str
    """``pods``, or ``pods/exec`` for a subresource."""
    namespace: str
    name: str

    def describe(self) -> str:
        what = f"{self.resource}.{self.group}" if self.group else self.resource
        target = f" {self.name}" if self.name else ""
        where = f" in namespace {self.namespace}" if self.namespace else " (cluster-wide)"
        return f"{self.verb} {what}{target}{where}"


@functools.cache
def _rules() -> tuple[dict, ...]:
    """Every rule of every ClusterRole the file binds to :data:`GROUP`."""
    import yaml

    docs = [d for d in yaml.safe_load_all(MANIFEST_PATH.read_text()) if d]
    roles = {d["metadata"]["name"]: d for d in docs if d["kind"] == "ClusterRole"}
    bound = {
        d["roleRef"]["name"]
        for d in docs
        if d["kind"] == "ClusterRoleBinding" and any(s.get("name") == GROUP for s in d.get("subjects") or [])
    }
    return tuple(rule for name in sorted(bound) for rule in roles[name].get("rules") or [])


def allows(verb: str, group: str, resource: str, name: str = "") -> bool:
    """Whether the minimal contract grants ``verb`` on ``resource`` in ``group``."""
    for rule in _rules():
        if group not in rule.get("apiGroups", ()) or resource not in rule.get("resources", ()):
            continue
        if verb not in rule.get("verbs", ()):
            continue
        names = rule.get("resourceNames")
        if names and name not in names:
            continue
        return True
    return False


def classify(method: str, path: str, query: dict | None = None) -> ApiRequest | None:
    """``method`` + ``path`` as an RBAC request; ``None`` for discovery and other non-resource URLs.

    Non-resource URLs (``/api``, ``/apis/<group>/<version>``, ``/version``,
    ``/openapi``) are granted to every authenticated user by the default
    ``system:discovery`` role, so the contract does not list them.
    """
    parts = [p for p in path.split("?")[0].split("/") if p]
    if parts[:2] == ["api", "v1"]:
        group, rest = "", parts[2:]
    elif parts[:1] == ["apis"] and len(parts) >= 3:
        group, rest = parts[1], parts[3:]
    else:
        return None
    if not rest:
        return None
    namespace = ""
    if rest[0] == "namespaces" and len(rest) >= 3 and rest[2] not in {"status", "finalize"}:
        namespace, rest = rest[1], rest[2:]
    resource, name = rest[0], rest[1] if len(rest) > 1 else ""
    if len(rest) > 2:
        resource = f"{resource}/{rest[2]}"
    method = method.upper()
    query = query or {}
    if resource.endswith(("/exec", "/portforward", "/attach")):
        # Websocket upgrades arrive as GET; the apiserver authorizes them as
        # create since Kubernetes 1.30. The contract grants both.
        verb = "create"
    elif method == "GET":
        if str(query.get("watch", "")).lower() in {"true", "1"}:
            verb = "watch"
        else:
            verb = "get" if name else "list"
    elif method == "DELETE":
        verb = "delete" if name else "deletecollection"
    else:
        verb = {"POST": "create", "PUT": "update", "PATCH": "patch"}.get(method, method.lower())
    return ApiRequest(verb=verb, group=group, resource=resource, namespace=namespace, name=name)


def enforced() -> bool:
    """Whether the install holds the control plane to the minimal contract."""
    from core.install_restrictions import reason

    return bool(reason("controllers"))


def refusal(request: ApiRequest) -> str:
    """Why ``request`` is refused under the minimal contract, or ``""``."""
    from core.install_restrictions import reason

    why = reason("controllers")
    if not why or allows(request.verb, request.group, request.resource, request.name):
        return ""
    return f"{why} Refused: {request.describe()}."


def host_is_eks(host: str) -> bool:
    """Whether ``host`` is an EKS apiserver endpoint."""
    return bool(_EKS_HOST.search(host or ""))


def _scoped_namespace_body(api_client, body):
    """``body`` for a namespace write, carrying :data:`SCOPE_LABEL`.

    Covers a create, a replace, a server-side apply and a merge patch, which
    all send the object (or part of it) as a mapping. A JSON patch (a list)
    is left alone; the admission policy refuses it if it drops the label.
    """
    if body is not None and not isinstance(body, dict | list | str | bytes):
        body = api_client.sanitize_for_serialization(body)
    if not isinstance(body, dict):
        return body
    metadata = dict(body.get("metadata") or {})
    metadata["labels"] = {**(metadata.get("labels") or {}), SCOPE_LABEL: "true"}
    return {**body, "metadata": metadata}


def guarded_call_api(original):
    """Wrap ``ApiClient.call_api`` so requests outside the contract never leave."""

    @functools.wraps(original)
    def call_api(self, resource_path, method, path_params=None, query_params=None, *args, **kwargs):
        if enforced():
            host = kwargs.get("_host") or getattr(getattr(self, "configuration", None), "host", "")
            if host_is_eks(host):
                path = resource_path
                if path_params:
                    for key, value in dict(path_params).items():
                        path = path.replace("{" + key + "}", str(value))
                request = classify(method, path, dict(query_params or []))
                if request is not None and (why := refusal(request)):
                    from core.install_restrictions import WithheldCapabilityError

                    raise WithheldCapabilityError(why)
                if (
                    request is not None
                    and request.group == ""
                    and request.resource == "namespaces"
                    and request.verb in {"create", "update", "patch"}
                ):
                    if "body" in kwargs:
                        kwargs["body"] = _scoped_namespace_body(self, kwargs["body"])
                    elif len(args) >= 2:
                        args = (args[0], _scoped_namespace_body(self, args[1]), *args[2:])
        return original(self, resource_path, method, path_params, query_params, *args, **kwargs)

    call_api._astrolift_rbac_guard = True
    return call_api


def install_transport_guard() -> bool:
    """Wrap the kubernetes client once per process. Returns whether it is in place."""
    try:
        from kubernetes.client import api_client
    except ImportError:
        return False
    current = api_client.ApiClient.call_api
    if not getattr(current, "_astrolift_rbac_guard", False):
        api_client.ApiClient.call_api = guarded_call_api(current)
    return True
