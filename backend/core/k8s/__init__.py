"""Kubernetes runtime adapters for the platform.

The control plane is mostly hands-off the runtime — workflow
activities talk to the cluster through provider-plugin drivers.
A handful of resolver-entry surfaces (the observability page,
pod-exec WS) still need to read live state directly. Those go
through this package.

Two flavors of access:

  ``client.client_for_cluster(cluster)`` -> a ready ``ApiClient``
    Builds an authenticated kubernetes-client ``ApiClient`` from a
    ``TenantCluster`` row. The auth method on the row decides which
    flavor of credentials we hand to the client library.

  ``pods.list_app_pods(...)`` / ``logs.stream_app_logs(...)``
    Higher-level adapters used by GraphQL resolvers + subscriptions.
    Both go through pluggable backends (``set_pod_backend`` /
    ``set_log_backend``) so tests can inject deterministic fakes
    without spinning up a real cluster.
"""

from __future__ import annotations
