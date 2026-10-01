"""Namespace-qualified names for generated in-cluster service bindings."""

from k8s_native.managed._handle import ParsedHandle


def service_host(parsed: ParsedHandle, *, name: str) -> str:
    # Let the pod's cluster DNS search suffix supply its configured domain.
    # Deriving a namespace from the resource name can target another tenant.
    if not parsed.cluster_id or not parsed.namespace:
        raise ValueError(
            "managed-service binding requires a recorded cluster and namespace; reconcile the legacy handle"
        )
    return f"{name}.{parsed.namespace}.svc"
