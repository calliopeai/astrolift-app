"""Defensive low-level wrappers around ``kubernetes.client.ApiClient`` (#765).

Background:  the high-level convenience methods that ship with the
python ``kubernetes`` package (e.g. ``ApiextensionsV1Api.list_custom_resource_definition``,
``CoreV1Api.list_namespaced_pod``) construct their own ``auth_settings``
and ``Content-Type`` headers internally.  That construction has drifted
across client versions — notably kubernetes-client ``36.0.0`` shipped
a regression that silently dropped the ``Authorization`` header for
inline-Bearer-token configurations against EKS, surfacing as a generic
401 Unauthorized with no indication that the token wasn't sent.

We pin ``kubernetes<36`` in ``backend/providers/pyproject.toml`` to
avoid that specific regression, but a future minor bump could hit a
similar drift.  The helpers in this module reach through to the
documented-stable low-level ``api_client.call_api(...)`` entry-point
and supply ``auth_settings=["BearerToken"]`` + the exact ``Content-Type``
each operation needs.  Same pattern proven in production AWS-deployed
Django apps that ship working EKS reads across many kubernetes-client
versions.

Coverage scope: the helpers wrap operations that the management +
probe paths actually use.  We intentionally keep the high-level API
the default elsewhere — switch to a wrapper only where breakage
matters (capability probe, namespace listing for the probe, manifest
apply for bring-into-management).
"""

from __future__ import annotations

from typing import Any


def get_server_version_dict(api_client: Any, *, timeout_seconds: int = 10) -> dict[str, Any]:
    """Read the apiserver ``/version`` document through the same explicit
    bearer-auth path used by the other management probes."""
    response = api_client.call_api(
        "/version",
        "GET",
        path_params={},
        query_params=[],
        header_params={
            "Accept": api_client.select_header_accept(["application/json"]),
        },
        body=None,
        post_params=[],
        files={},
        response_type="object",
        auth_settings=["BearerToken"],
        async_req=False,
        _return_http_data_only=True,
        _preload_content=True,
        _request_timeout=timeout_seconds,
    )
    return dict(response or {})


def list_cluster_crd_names(api_client: Any, *, timeout_seconds: int = 10) -> list[str]:
    """List CRD names via low-level call_api — used by the capability
    probe.  Equivalent to
    ``ApiextensionsV1Api(api_client).list_custom_resource_definition(...)``
    but bypasses high-level method drift.

    Returns a sorted list of CRD ``metadata.name`` strings.  Raises
    ``kubernetes.client.exceptions.ApiException`` on transport / auth
    failures so callers see the same error type as the high-level path.
    """
    response = api_client.call_api(
        "/apis/apiextensions.k8s.io/v1/customresourcedefinitions",
        "GET",
        path_params={},
        query_params=[("timeoutSeconds", timeout_seconds)],
        header_params={
            "Accept": api_client.select_header_accept(["application/json"]),
        },
        body=None,
        post_params=[],
        files={},
        response_type="object",
        auth_settings=["BearerToken"],
        async_req=False,
        _return_http_data_only=True,
        _preload_content=True,
    )
    items = (response or {}).get("items") or []
    return sorted(
        (item.get("metadata") or {}).get("name", "") for item in items if (item.get("metadata") or {}).get("name")
    )


def list_namespaced_pod_dicts(
    api_client: Any,
    *,
    namespace: str,
    label_selector: str | None = None,
    timeout_seconds: int = 10,
) -> list[dict[str, Any]]:
    """List pods in a namespace via low-level call_api.  Returns the
    raw pod dicts (no kubernetes-model deserialization) so callers
    that only need a handful of fields don't pay the model-mapping
    cost.

    ``label_selector`` is forwarded as-is when present.  Missing or
    empty namespace returns an empty list (matches the high-level
    method's behavior on a not-found namespace via the swallowed
    404 the probe relies on)."""
    if not namespace:
        return []
    query_params: list[tuple[str, Any]] = [("timeoutSeconds", timeout_seconds)]
    if label_selector:
        query_params.append(("labelSelector", label_selector))
    response = api_client.call_api(
        f"/api/v1/namespaces/{namespace}/pods",
        "GET",
        path_params={"namespace": namespace},
        query_params=query_params,
        header_params={
            "Accept": api_client.select_header_accept(["application/json"]),
        },
        body=None,
        post_params=[],
        files={},
        response_type="object",
        auth_settings=["BearerToken"],
        async_req=False,
        _return_http_data_only=True,
        _preload_content=True,
    )
    return list((response or {}).get("items") or [])
