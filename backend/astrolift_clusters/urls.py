"""REST routes for the cluster keep-alive agent (#808).

Mounted at the project root (NOT under the ``/app/`` UI prefix) so the
wire URL the in-cluster agent ships against —
``/api/clusters/v1/<cluster_guid>/heartbeat/`` — resolves without
rewriting the agent. Same mounting convention as the Dispatch Service
and self-hosted runner REST surfaces.
"""

from __future__ import annotations

from django.urls import path

from astrolift_clusters.cloudflare_dns_oauth import callback as cloudflare_dns_callback
from astrolift_clusters.views_heartbeat import cluster_heartbeat, cluster_test_result

app_name = "astrolift_clusters"

urlpatterns = [
    path("api/clusters/dns/cloudflare/callback/", cloudflare_dns_callback, name="cloudflare-dns-callback"),
    path(
        "api/clusters/v1/<str:cluster_guid>/heartbeat/",
        cluster_heartbeat,
        name="cluster-heartbeat",
    ),
    path(
        "api/clusters/v1/<str:cluster_guid>/model-test-result/",
        cluster_test_result,
        name="cluster-model-test-result",
    ),
]
