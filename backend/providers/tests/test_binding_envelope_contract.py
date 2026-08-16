"""Cross-provider binding-envelope contract for every registered driver (#1398).

``tests/aws/test_binding_envelope.py`` pins the #1003 contract -- a driver's
``binding()`` keys must be a superset of the canonical envelope in
``astrolift_manifest.env_injection._ENVELOPES`` -- but it does so with three
hand-written AWS cases. Everything outside those three was unguarded, and the
same defect shipped green three times in a row:

* ``azure/managed/private_endpoint.py`` emitted ``PRIVATE_ENDPOINT_FQDNS``,
  which is not on the ``private_endpoint`` allow-list.
* ``azure/managed/faas_functions.py`` emitted ``FUNCTION_RESOURCE_ID`` and
  ``FUNCTION_PROVIDER``, neither on the ``faas`` allow-list.
* ``k8s_native/managed/filesystem_pvc.py`` omitted ``FILESYSTEM_TLS``, the one
  filesystem driver that did (#1398).

Every one of those passed its own unit tests, because those tests assert on the
driver's output rather than on the platform contract that output has to satisfy.
This module closes that gap for all of them at once.

Why this reads source instead of calling ``binding()``
------------------------------------------------------
89 of the 141 registered drivers cannot be constructed in a test process: the
AWS drivers validate their region against a regex at ``__init__`` time and the
GCP drivers build a real API client, which needs application-default
credentials. There is therefore no generic way to *call* ``binding()`` across
the registry, and ``binding_schema()`` is not a usable stand-in -- it has drifted
badly (``azure/managed/postgres_flexible.py`` still declares the pre-#1003
``DATABASE_*`` names while its ``binding()`` emits them too, and
``gcp/managed/object_store_gcs.py`` declares ``GCS_*`` only).

So the contract is checked statically, against the ``binding()`` body itself,
which is the thing that actually produces the ``ManagedServiceBinding`` rows.
Extraction failure is a test failure, not a skip: a driver whose ``binding()``
this module cannot read has to be listed in ``_UNREADABLE_BINDINGS`` with an
issue number.

What "provider alias" means
---------------------------
Drivers legitimately emit provider-native names beside the portable envelope --
``azure/managed/filesystem_files.py`` ships ``AZURE_FILE_SHARE_NAME`` and
``azure/managed/faas_functions.py`` ships ``AZURE_FUNCTION_APP_NAME``. Those are
fine: nothing reads them as portable. What is *not* fine is a key that wears the
envelope's own prefix without being in the envelope, because that is a key an
app author will reasonably assume is portable. ``FUNCTION_RESOURCE_ID`` is
exactly that, and the fix comment in ``faas_functions.py`` says so in prose:
"Only the keys in the platform's faas envelope are injected into a workload, so
the ARM resource ID has to land here rather than in a driver-local
FUNCTION_RESOURCE_ID." ``_RESERVED_PREFIXES`` turns that prose into a rule.

Known divergences
-----------------
The ledgers below record pre-existing divergences exactly, not as blanket
skips. Each entry pins the precise divergence, so a driver that regresses
further fails, and a driver that gets fixed also fails with "stale ledger
entry". The ledgers can only shrink.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from astrolift_manifest.env_injection import _ENVELOPES, envelope_keys_for

from aws.plugin import PLUGIN as AWS_PLUGIN
from azure.plugin import PLUGIN as AZURE_PLUGIN
from gcp.plugin import PLUGIN as GCP_PLUGIN
from k8s_native.plugin import PLUGIN as K8S_PLUGIN

if TYPE_CHECKING:
    from collections.abc import Iterator

_PLUGINS = (AWS_PLUGIN, AZURE_PLUGIN, GCP_PLUGIN, K8S_PLUGIN)


# --------------------------------------------------------------------------
# Contract data
# --------------------------------------------------------------------------

# The env-var prefixes each kind's envelope owns. A key carrying one of these
# is claiming portability, so it must be in ``_ENVELOPES``; anything else is a
# provider-local alias and is left alone. Kept explicit rather than derived,
# because the derivation is ambiguous (``event_bus`` owns ``EVENT_BUS_`` but
# not ``EVENT_``, or Azure's legitimate ``EVENT_GRID_*`` aliases would trip).
# ``test_reserved_prefixes_cover_every_envelope`` keeps this honest: it fails
# if a kind is missing or if an envelope key falls outside its own prefixes.
_RESERVED_PREFIXES: dict[str, tuple[str, ...]] = {
    "api_gateway": ("API_GATEWAY_",),
    "cache": ("CACHE_",),
    "cdn": ("CDN_",),
    "database_proxy": ("DATABASE_PROXY_",),
    "document_db": ("DOCDB_",),
    "email": ("EMAIL_",),
    "encryption_key": ("ENCRYPTION_KEY_",),
    "event_bus": ("EVENT_BUS_",),
    "event_stream": ("EVENT_STREAM_",),
    "faas": ("FUNCTION_",),
    "filesystem": ("FILESYSTEM_",),
    "graph_db": ("GRAPH_DB_",),
    "kv_store": ("KV_",),
    "model_endpoint": ("MODEL_",),
    "mq": ("MQ_", "KAFKA_"),
    "mssql": ("MSSQL_", "DATABASE_"),
    "mysql": ("MYSQL_", "DATABASE_"),
    "nfs": ("NFS_",),
    "object_store": ("BUCKET_",),
    "observability": ("OBSERVABILITY_", "LOG_GROUP", "METRICS_", "DASHBOARD_"),
    "postgres": ("POSTGRES_", "DATABASE_"),
    "private_endpoint": ("PRIVATE_ENDPOINT_",),
    "queue": ("QUEUE_",),
    "redis": ("REDIS_",),
    "search": ("SEARCH_",),
    "sms": ("SMS_",),
    "stream": ("STREAM_",),
    "time_series": ("TS_",),
    "topic": ("TOPIC_",),
    "vector_index": ("VECTOR_",),
    "warehouse": ("WAREHOUSE_",),
    "wide_column": ("WIDE_COLUMN_",),
    "workflow_engine": ("WORKFLOW_ENGINE_",),
}


# Drivers whose ``binding()`` this module cannot read statically. Each needs an
# issue; none may be added without one. Empty since #1400: the one entry,
# ``gcp/managed/event_stream_managed_kafka.py``, built its mTLS env keys from a
# loop variable and now names them literally, so every registered driver's
# binding() is readable and the ledgers below cover the whole registry.
_UNREADABLE_BINDINGS: dict[tuple[str, str, str], str] = {}


# Drivers that never return a Binding at all -- their binding() raises. They
# emit no envelope by construction and take no part in the cross-driver
# comparisons, so the set is pinned here to keep that from spreading.
_NEVER_BINDS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("gcp", "email", "gcp_thirdparty"),
        ("gcp", "search", "gcp_elastic_cloud"),
    },
)


# Keys a driver emits that wear their kind's reserved prefix but are not in
# ``_ENVELOPES``. Value is the exact set of offending keys for that driver.
# https://github.com/calliopeai/astrolift-app/issues/1401
_UNDECLARED_ENVELOPE_KEYS: dict[tuple[str, str, str], frozenset[str]] = {
    ("aws", "email", "ses"): frozenset(
        {
            "EMAIL_FROM_ADDRESS",
            "EMAIL_FROM_ADDRESS_PREVIEW",
            "EMAIL_FROM_ADDRESS_PRODUCTION",
            "EMAIL_FROM_NAME",
            "EMAIL_REGION",
            "EMAIL_REPLY_TO",
            "EMAIL_RETURN_PATH",
        }
    ),
    ("aws", "encryption_key", "kms"): frozenset(
        {"ENCRYPTION_KEY_MULTI_REGION", "ENCRYPTION_KEY_SPEC", "ENCRYPTION_KEY_USAGE"}
    ),
    ("aws", "event_stream", "msk"): frozenset(
        {"EVENT_STREAM_AUTH_MECHANISM", "EVENT_STREAM_CA_CERT", "EVENT_STREAM_CLIENT_CERT", "EVENT_STREAM_CLIENT_KEY"}
    ),
    ("aws", "event_stream", "msk_serverless"): frozenset(
        {"EVENT_STREAM_AUTH_MECHANISM", "EVENT_STREAM_CA_CERT", "EVENT_STREAM_CLIENT_CERT", "EVENT_STREAM_CLIENT_KEY"}
    ),
    ("aws", "filesystem", "efs"): frozenset({"FILESYSTEM_ENDPOINT", "FILESYSTEM_MOUNT_OPTIONS", "FILESYSTEM_PROTOCOL"}),
    ("aws", "filesystem", "fsx_lustre"): frozenset(
        {
            "FILESYSTEM_ENDPOINT",
            "FILESYSTEM_MOUNT_OPTIONS",
            "FILESYSTEM_MOUNT_SOURCE",
            "FILESYSTEM_PASSWORD",
            "FILESYSTEM_PROTOCOL",
            "FILESYSTEM_USERNAME",
        }
    ),
    ("aws", "filesystem", "fsx_openzfs"): frozenset(
        {
            "FILESYSTEM_ENDPOINT",
            "FILESYSTEM_MOUNT_OPTIONS",
            "FILESYSTEM_MOUNT_SOURCE",
            "FILESYSTEM_PASSWORD",
            "FILESYSTEM_PROTOCOL",
            "FILESYSTEM_USERNAME",
        }
    ),
    ("aws", "filesystem", "fsx_windows"): frozenset(
        {
            "FILESYSTEM_ENDPOINT",
            "FILESYSTEM_MOUNT_OPTIONS",
            "FILESYSTEM_MOUNT_SOURCE",
            "FILESYSTEM_PASSWORD",
            "FILESYSTEM_PROTOCOL",
            "FILESYSTEM_USERNAME",
        }
    ),
    ("aws", "model_endpoint", "bedrock"): frozenset({"MODEL_ENDPOINT_MODEL_ID", "MODEL_ENDPOINT_PROVIDER"}),
    ("aws", "mq", "amazon_mq_activemq"): frozenset({"MQ_AUTH_STRATEGY"}),
    ("aws", "mq", "amazon_mq_rabbitmq"): frozenset({"MQ_AUTH_STRATEGY"}),
    ("aws", "mysql", "rds_mysql"): frozenset(
        {"DATABASE_HOST", "DATABASE_NAME", "DATABASE_PASSWORD", "DATABASE_PORT", "DATABASE_USER"}
    ),
    ("aws", "private_endpoint", "vpc_endpoint"): frozenset(
        {
            "PRIVATE_ENDPOINT_DNS_NAME",
            "PRIVATE_ENDPOINT_DNS_NAMES",
            "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS",
            "PRIVATE_ENDPOINT_PREFIX_LIST_ID",
            "PRIVATE_ENDPOINT_SERVICE_NAME",
            "PRIVATE_ENDPOINT_TYPE",
        }
    ),
    ("aws", "search", "opensearch"): frozenset({"SEARCH_API_KEY", "SEARCH_URL"}),
    ("aws", "workflow_engine", "step_functions_express"): frozenset({"WORKFLOW_ENGINE_TYPE"}),
    ("aws", "workflow_engine", "step_functions_standard"): frozenset({"WORKFLOW_ENGINE_TYPE"}),
    ("azure", "api_gateway", "api_management"): frozenset({"API_GATEWAY_PROVIDER"}),
    ("azure", "email", "azure_acs"): frozenset({"EMAIL_FROM_ADDRESS", "EMAIL_REGION"}),
    ("azure", "event_bus", "event_grid"): frozenset({"EVENT_BUS_ENDPOINT"}),
    ("azure", "event_bus", "event_grid_namespace"): frozenset({"EVENT_BUS_ENDPOINT"}),
    ("azure", "filesystem", "azure_files"): frozenset(
        {
            "FILESYSTEM_ENDPOINT",
            "FILESYSTEM_EXPORT_PATH",
            "FILESYSTEM_MOUNT_OPTIONS",
            "FILESYSTEM_PROTOCOL",
            "FILESYSTEM_READ_ONLY",
            "FILESYSTEM_SOURCE",
        }
    ),
    ("azure", "filesystem", "azure_files_classic"): frozenset(
        {
            "FILESYSTEM_ENDPOINT",
            "FILESYSTEM_EXPORT_PATH",
            "FILESYSTEM_MOUNT_OPTIONS",
            "FILESYSTEM_PASSWORD",
            "FILESYSTEM_PASSWORD_SECONDARY",
            "FILESYSTEM_PROTOCOL",
            "FILESYSTEM_READ_ONLY",
            "FILESYSTEM_SOURCE",
            "FILESYSTEM_USERNAME",
        }
    ),
    ("azure", "model_endpoint", "azure_openai"): frozenset({"MODEL_ENDPOINT_MODEL_ID", "MODEL_ENDPOINT_PROVIDER"}),
    ("azure", "mysql", "azure_mysql_flex"): frozenset(
        {"DATABASE_HOST", "DATABASE_NAME", "DATABASE_PASSWORD", "DATABASE_PORT", "DATABASE_USER"}
    ),
    ("azure", "postgres", "azure_pg_flex"): frozenset(
        {"DATABASE_HOST", "DATABASE_NAME", "DATABASE_PASSWORD", "DATABASE_PORT", "DATABASE_USER"}
    ),
    ("azure", "private_endpoint", "private_link"): frozenset(
        {
            "PRIVATE_ENDPOINT_DNS_NAME",
            "PRIVATE_ENDPOINT_DNS_NAMES",
            "PRIVATE_ENDPOINT_NAME",
            "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS",
            "PRIVATE_ENDPOINT_SERVICE_NAME",
            "PRIVATE_ENDPOINT_TYPE",
        }
    ),
    ("azure", "redis", "azure_cache_redis"): frozenset(
        {"REDIS_AUTH_TOKEN", "REDIS_PORT_NON_SSL", "REDIS_SECONDARY_AUTH_TOKEN"}
    ),
    ("azure", "redis", "azure_managed_redis"): frozenset({"REDIS_AUTH_TOKEN", "REDIS_SECONDARY_AUTH_TOKEN"}),
    ("azure", "search", "azure_ai_search_fulltext"): frozenset({"SEARCH_API_KEY", "SEARCH_URL"}),
    ("gcp", "api_gateway", "api_gateway"): frozenset({"API_GATEWAY_HOST"}),
    ("gcp", "cdn", "cloud_cdn"): frozenset({"CDN_DOMAIN", "CDN_IP_ADDRESS", "CDN_URL"}),
    ("gcp", "document_db", "firestore_native"): frozenset({"DOCDB_AUTH_MODE"}),
    ("gcp", "encryption_key", "cloud_kms"): frozenset(
        {"ENCRYPTION_KEY_MULTI_REGION", "ENCRYPTION_KEY_SPEC", "ENCRYPTION_KEY_USAGE"}
    ),
    ("gcp", "event_bus", "eventarc"): frozenset({"EVENT_BUS_ID", "EVENT_BUS_PROVIDER", "EVENT_BUS_PUBLISH_URL"}),
    # Pre-existing, and the same four keys ``aws/event_stream/msk`` diverges on.
    # Only became visible when #1400 made this driver's binding() readable.
    ("gcp", "event_stream", "managed_kafka"): frozenset(
        {"EVENT_STREAM_AUTH_MECHANISM", "EVENT_STREAM_CA_CERT", "EVENT_STREAM_CLIENT_CERT", "EVENT_STREAM_CLIENT_KEY"}
    ),
    ("gcp", "filesystem", "filestore"): frozenset(
        {"FILESYSTEM_ENDPOINT", "FILESYSTEM_EXPORT", "FILESYSTEM_MOUNT_OPTIONS", "FILESYSTEM_PROTOCOL"}
    ),
    ("gcp", "model_endpoint", "vertex_ai"): frozenset({"MODEL_ENDPOINT_MODEL_ID", "MODEL_ENDPOINT_PROVIDER"}),
    ("gcp", "private_endpoint", "private_service_connect"): frozenset(
        {
            "PRIVATE_ENDPOINT_DNS_NAME",
            "PRIVATE_ENDPOINT_DNS_NAMES",
            "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS",
            "PRIVATE_ENDPOINT_PREFIX_LIST_ID",
            "PRIVATE_ENDPOINT_SERVICE_NAME",
            "PRIVATE_ENDPOINT_TYPE",
            "PRIVATE_ENDPOINT_URL",
        }
    ),
    ("gcp", "redis", "memorystore_valkey"): frozenset({"REDIS_CA_CERT", "REDIS_READER_URL"}),
    ("gcp", "workflow_engine", "workflows"): frozenset({"WORKFLOW_ENGINE_TYPE"}),
    ("k8s_native", "api_gateway", "gateway_api"): frozenset(
        {"API_GATEWAY_HOST", "API_GATEWAY_NAMESPACE", "API_GATEWAY_PORT"}
    ),
    ("k8s_native", "event_bus", "knative_eventing"): frozenset({"EVENT_BUS_ENDPOINT", "EVENT_BUS_URI"}),
    ("k8s_native", "model_endpoint", "kserve"): frozenset(
        {"MODEL_ENDPOINT_GRPC_URL", "MODEL_FORMAT", "MODEL_PROTOCOL_VERSION", "MODEL_SERVING_RUNTIME"}
    ),
    ("k8s_native", "mssql", "sqlserver_express"): frozenset({"MSSQL_TRUST_SERVER_CERTIFICATE"}),
    ("k8s_native", "observability", "kube_prometheus_stack"): frozenset(
        {"OBSERVABILITY_BUNDLE", "OBSERVABILITY_NAMESPACE"}
    ),
    ("k8s_native", "postgres", "cnpg"): frozenset(
        {"DATABASE_HOST", "DATABASE_NAME", "DATABASE_PASSWORD", "DATABASE_PORT", "DATABASE_USER"}
    ),
    ("k8s_native", "search", "opensearch_operator"): frozenset({"SEARCH_TLS_VERIFY"}),
    ("k8s_native", "vector_index", "opensearch_operator_vector"): frozenset(
        {"VECTOR_PASSWORD", "VECTOR_TLS_VERIFY", "VECTOR_USERNAME"}
    ),
    ("k8s_native", "workflow_engine", "argo_workflows"): frozenset({"WORKFLOW_ENGINE_TYPE"}),
}


# Drivers of a kind that do not emit the same envelope subset as their
# siblings. Value is the exact set of envelope keys the driver is missing
# relative to the union emitted across that kind.
# https://github.com/calliopeai/astrolift-app/issues/1402
_ENVELOPE_SUBSET_DIVERGENCE: dict[tuple[str, str, str], frozenset[str]] = {
    ("aws", "faas", "lambda"): frozenset({"FUNCTION_ARN", "FUNCTION_REGION"}),
    ("aws", "model_endpoint", "bedrock"): frozenset({"MODEL_DEPLOYMENT_NAME", "MODEL_REGION"}),
    ("aws", "mysql", "rds_mysql"): frozenset({"MYSQL_DB", "MYSQL_HOST", "MYSQL_PASSWORD", "MYSQL_PORT", "MYSQL_USER"}),
    ("aws", "postgres", "aurora_postgres"): frozenset(
        {
            "POSTGRES_DB",
            "POSTGRES_HOST",
            "POSTGRES_MASTER_SECRET_REF",
            "POSTGRES_PASSWORD",
            "POSTGRES_PORT",
            "POSTGRES_SSL_MODE",
            "POSTGRES_USER",
        }
    ),
    ("aws", "postgres", "aurora_postgres_serverless_v2"): frozenset(
        {
            "POSTGRES_DB",
            "POSTGRES_HOST",
            "POSTGRES_MASTER_SECRET_REF",
            "POSTGRES_PASSWORD",
            "POSTGRES_PORT",
            "POSTGRES_SSL_MODE",
            "POSTGRES_USER",
        }
    ),
    ("aws", "postgres", "rds"): frozenset({"POSTGRES_MASTER_SECRET_REF"}),
    ("aws", "redis", "elasticache"): frozenset({"REDIS_AUTH_MODE", "REDIS_RESOURCE_ARN"}),
    ("aws", "redis", "elasticache_valkey"): frozenset({"REDIS_AUTH_MODE", "REDIS_RESOURCE_ARN"}),
    ("aws", "search", "opensearch"): frozenset({"SEARCH_ENDPOINT", "SEARCH_PASSWORD", "SEARCH_USER"}),
    ("aws", "search", "opensearch_serverless"): frozenset({"SEARCH_PASSWORD", "SEARCH_USER"}),
    ("aws", "vector_index", "opensearch_vector"): frozenset(
        {"VECTOR_ENDPOINT", "VECTOR_INDEX_NAME", "VECTOR_NAMESPACE"}
    ),
    ("aws", "warehouse", "redshift"): frozenset({"WAREHOUSE_NAMESPACE", "WAREHOUSE_WORKGROUP"}),
    ("aws", "warehouse", "redshift_serverless"): frozenset({"WAREHOUSE_CLUSTER_ID"}),
    ("azure", "api_gateway", "api_management"): frozenset({"API_GATEWAY_STAGE"}),
    ("azure", "document_db", "cosmos_mongodb"): frozenset(
        {"DOCDB_DB", "DOCDB_PASSWORD", "DOCDB_RESOURCE_ARN", "DOCDB_TLS", "DOCDB_URI", "DOCDB_USER"}
    ),
    ("azure", "document_db", "cosmos_nosql"): frozenset(
        {"DOCDB_DB", "DOCDB_PASSWORD", "DOCDB_RESOURCE_ARN", "DOCDB_TLS", "DOCDB_URI", "DOCDB_USER"}
    ),
    ("azure", "event_stream", "event_hubs_kafka"): frozenset(
        {"EVENT_STREAM_BROKERS", "EVENT_STREAM_PASSWORD", "EVENT_STREAM_TLS", "EVENT_STREAM_USERNAME"}
    ),
    ("azure", "graph_db", "cosmos_gremlin"): frozenset(
        {
            "GRAPH_DB_AUTH_MODE",
            "GRAPH_DB_ENDPOINT",
            "GRAPH_DB_PORT",
            "GRAPH_DB_PROTOCOL",
            "GRAPH_DB_READER_URL",
            "GRAPH_DB_REGION",
            "GRAPH_DB_RESOURCE_ARN",
            "GRAPH_DB_TLS",
            "GRAPH_DB_URL",
        }
    ),
    ("azure", "model_endpoint", "azure_openai"): frozenset({"MODEL_DEPLOYMENT_NAME", "MODEL_REGION"}),
    ("azure", "mysql", "azure_mysql_flex"): frozenset(
        {"MYSQL_DB", "MYSQL_HOST", "MYSQL_PASSWORD", "MYSQL_PORT", "MYSQL_USER"}
    ),
    ("azure", "object_store", "azure_blob"): frozenset(
        {"BUCKET_ENDPOINT", "BUCKET_NAME", "BUCKET_PREFIX", "BUCKET_REGION"}
    ),
    ("azure", "object_store", "blob"): frozenset({"BUCKET_ENDPOINT", "BUCKET_NAME", "BUCKET_PREFIX", "BUCKET_REGION"}),
    ("azure", "postgres", "azure_pg_flex"): frozenset(
        {
            "POSTGRES_DB",
            "POSTGRES_HOST",
            "POSTGRES_MASTER_SECRET_REF",
            "POSTGRES_PASSWORD",
            "POSTGRES_PORT",
            "POSTGRES_SSL_MODE",
            "POSTGRES_USER",
        }
    ),
    ("azure", "queue", "azure_servicebus"): frozenset({"QUEUE_ARN_OR_ID", "QUEUE_NAME", "QUEUE_REGION", "QUEUE_URL"}),
    ("azure", "queue", "servicebus"): frozenset({"QUEUE_ARN_OR_ID", "QUEUE_NAME", "QUEUE_REGION", "QUEUE_URL"}),
    ("azure", "redis", "azure_cache_redis"): frozenset(
        {"REDIS_AUTH_MODE", "REDIS_PASSWORD", "REDIS_RESOURCE_ARN", "REDIS_USER"}
    ),
    ("azure", "redis", "azure_managed_redis"): frozenset(
        {"REDIS_AUTH_MODE", "REDIS_PASSWORD", "REDIS_RESOURCE_ARN", "REDIS_USER"}
    ),
    ("azure", "search", "azure_ai_search_fulltext"): frozenset({"SEARCH_ENDPOINT", "SEARCH_PASSWORD", "SEARCH_USER"}),
    ("azure", "stream", "event_hubs"): frozenset({"STREAM_ARN", "STREAM_ENDPOINT", "STREAM_NAME", "STREAM_REGION"}),
    ("azure", "topic", "service_bus_topic"): frozenset({"TOPIC_ARN_OR_ID", "TOPIC_NAME", "TOPIC_REGION"}),
    ("azure", "vector_index", "azure_ai_search_vector"): frozenset(
        {"VECTOR_ENDPOINT", "VECTOR_INDEX_NAME", "VECTOR_NAMESPACE"}
    ),
    ("azure", "wide_column", "cosmos_cassandra"): frozenset(
        {
            "WIDE_COLUMN_AUTH_MODE",
            "WIDE_COLUMN_ENDPOINT",
            "WIDE_COLUMN_KEYSPACE",
            "WIDE_COLUMN_PORT",
            "WIDE_COLUMN_REGION",
            "WIDE_COLUMN_RESOURCE_ARN",
            "WIDE_COLUMN_TABLE",
        }
    ),
    ("gcp", "api_gateway", "api_gateway"): frozenset({"API_GATEWAY_STAGE"}),
    ("gcp", "document_db", "firestore_native"): frozenset({"DOCDB_PASSWORD", "DOCDB_USER"}),
    ("gcp", "event_bus", "eventarc"): frozenset({"EVENT_BUS_ARN"}),
    # Pre-existing: Managed Kafka authenticates with Google IAM, so it has no
    # username/password to emit. Only became visible when #1400 made this
    # driver's binding() readable.
    ("gcp", "event_stream", "managed_kafka"): frozenset({"EVENT_STREAM_PASSWORD", "EVENT_STREAM_USERNAME"}),
    ("gcp", "faas", "cloud_functions_gen2"): frozenset({"FUNCTION_ARN", "FUNCTION_REGION"}),
    ("gcp", "graph_db", "spanner_graph"): frozenset({"GRAPH_DB_PORT", "GRAPH_DB_READER_URL"}),
    ("gcp", "model_endpoint", "vertex_ai"): frozenset({"MODEL_DEPLOYMENT_NAME", "MODEL_REGION"}),
    ("gcp", "object_store", "gcs"): frozenset({"BUCKET_ENDPOINT", "BUCKET_NAME", "BUCKET_PREFIX", "BUCKET_REGION"}),
    ("gcp", "queue", "pubsub"): frozenset({"QUEUE_ARN_OR_ID", "QUEUE_NAME", "QUEUE_REGION", "QUEUE_URL"}),
    ("gcp", "vector_index", "vertex_matching_engine"): frozenset(
        {"VECTOR_ENDPOINT", "VECTOR_INDEX_NAME", "VECTOR_NAMESPACE"}
    ),
    ("gcp", "warehouse", "bigquery"): frozenset(
        {
            "WAREHOUSE_CREDENTIALS_REF",
            "WAREHOUSE_NAMESPACE",
            "WAREHOUSE_PORT",
            "WAREHOUSE_TLS",
            "WAREHOUSE_USER",
            "WAREHOUSE_WORKGROUP",
        }
    ),
    ("k8s_native", "api_gateway", "gateway_api"): frozenset({"API_GATEWAY_STAGE"}),
    ("k8s_native", "document_db", "mongodb_operator"): frozenset({"DOCDB_RESOURCE_ARN", "DOCDB_TLS"}),
    ("k8s_native", "event_stream", "nats"): frozenset({"EVENT_STREAM_PASSWORD", "EVENT_STREAM_USERNAME"}),
    ("k8s_native", "mysql", "operator"): frozenset({"DATABASE_URL"}),
    ("k8s_native", "observability", "kube_prometheus_stack"): frozenset({"LOG_GROUP"}),
    ("k8s_native", "postgres", "cnpg"): frozenset(
        {
            "POSTGRES_DB",
            "POSTGRES_HOST",
            "POSTGRES_MASTER_SECRET_REF",
            "POSTGRES_PASSWORD",
            "POSTGRES_PORT",
            "POSTGRES_SSL_MODE",
            "POSTGRES_USER",
        }
    ),
    ("k8s_native", "queue", "rabbitmq_operator"): frozenset(
        {"QUEUE_ARN_OR_ID", "QUEUE_NAME", "QUEUE_REGION", "QUEUE_URL"}
    ),
    ("k8s_native", "redis", "operator"): frozenset(
        {"REDIS_AUTH_MODE", "REDIS_RESOURCE_ARN", "REDIS_TLS", "REDIS_USER"}
    ),
    ("k8s_native", "vector_index", "opensearch_operator_vector"): frozenset({"VECTOR_NAMESPACE"}),
}


# Keys emitted by more than one driver with disagreeing value formats. Value is
# the exact mapping of format class -> the drivers using it.
# https://github.com/calliopeai/astrolift-app/issues/1403
_VALUE_FORMAT_DIVERGENCE: dict[tuple[str, str], dict[str, frozenset[str]]] = {
    ("cache", "CACHE_NODES"): {
        "join(',')": frozenset({"aws/cache/elasticache_memcached"}),
        "scalar": frozenset({"aws/cache/elasticache_serverless_memcached"}),
    },
    ("document_db", "DOCDB_URI"): {
        "scalar": frozenset({"gcp/document_db/firestore_native", "k8s_native/document_db/mongodb_operator"}),
        "secret_ref": frozenset({"aws/document_db/documentdb", "aws/document_db/documentdb_serverless_v2"}),
    },
    ("document_db", "DOCDB_USER"): {
        "scalar": frozenset({"aws/document_db/documentdb", "aws/document_db/documentdb_serverless_v2"}),
        "secret_ref": frozenset({"k8s_native/document_db/mongodb_operator"}),
    },
    ("filesystem", "FILESYSTEM_USERNAME"): {
        "scalar": frozenset({"azure/filesystem/azure_files_classic"}),
        "secret_ref": frozenset(
            {"aws/filesystem/fsx_lustre", "aws/filesystem/fsx_openzfs", "aws/filesystem/fsx_windows"}
        ),
    },
    ("mysql", "MYSQL_USER"): {
        "scalar": frozenset({"aws/mysql/aurora_mysql", "aws/mysql/aurora_mysql_serverless_v2", "gcp/mysql/cloudsql"}),
        "secret_ref": frozenset({"k8s_native/mysql/operator"}),
    },
    ("postgres", "DATABASE_HOST"): {
        "scalar": frozenset({"azure/postgres/azure_pg_flex"}),
        "secret_ref": frozenset({"k8s_native/postgres/cnpg"}),
    },
    ("postgres", "DATABASE_NAME"): {
        "scalar": frozenset({"azure/postgres/azure_pg_flex"}),
        "secret_ref": frozenset({"k8s_native/postgres/cnpg"}),
    },
    ("postgres", "DATABASE_PORT"): {
        "scalar": frozenset({"azure/postgres/azure_pg_flex"}),
        "secret_ref": frozenset({"k8s_native/postgres/cnpg"}),
    },
    ("postgres", "DATABASE_USER"): {
        "scalar": frozenset({"azure/postgres/azure_pg_flex"}),
        "secret_ref": frozenset({"k8s_native/postgres/cnpg"}),
    },
    ("redis", "REDIS_URL"): {
        "scalar": frozenset(
            {
                "aws/redis/elasticache",
                "aws/redis/elasticache_serverless_redis",
                "aws/redis/elasticache_serverless_valkey",
                "aws/redis/elasticache_valkey",
                "aws/redis/memorydb",
                "gcp/redis/memorystore",
                "k8s_native/redis/operator",
            }
        ),
        "secret_ref": frozenset(
            {"azure/redis/azure_cache_redis", "azure/redis/azure_managed_redis", "gcp/redis/memorystore_valkey"}
        ),
    },
}


# --------------------------------------------------------------------------
# Registry walk
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Driver:
    plugin_id: str
    kind: str
    variant: str
    cls: type[Any]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.plugin_id, self.kind, self.variant)

    @property
    def label(self) -> str:
        return f"{self.plugin_id}/{self.kind}/{self.variant}"


def _registered_drivers() -> tuple[_Driver, ...]:
    out: list[_Driver] = []
    for plugin in _PLUGINS:
        for (kind, variant), cls in sorted(plugin.managed_service_drivers.items()):
            out.append(_Driver(plugin_id=plugin.id, kind=kind, variant=variant, cls=cls))
    return tuple(out)


DRIVERS = _registered_drivers()


# --------------------------------------------------------------------------
# Static extraction of binding()
# --------------------------------------------------------------------------


class _Unreadable(Exception):
    """``binding()`` does not expose its env keys to static reading."""


class _NoBinding(Exception):
    """``binding()`` unconditionally raises, so the driver emits no envelope.

    The two GCP third-party stubs are like this. They are not unreadable --
    their env surface is knowably empty -- so they take no part in the
    cross-driver comparisons.
    """


_MAX_DEPTH = 6


@dataclass(frozen=True)
class _Scope:
    """Everything needed to resolve a name or call inside one function body."""

    cls: type[Any]
    module: Any
    assignments: dict[str, list[ast.expr]]


def _function_ast(func: Any) -> ast.FunctionDef:
    func = inspect.unwrap(getattr(func, "__wrapped__", func))
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError) as exc:  # pragma: no cover - defensive
        raise _Unreadable(f"no source for {func!r}") from exc
    node = ast.parse(source).body[0]
    if not isinstance(node, ast.FunctionDef):
        raise _Unreadable(f"{func!r} is not a plain function")
    return node


def _executable_body(fn: ast.FunctionDef) -> list[ast.stmt]:
    """``fn``'s statements minus its docstring."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _owner_of(cls: type[Any], name: str) -> type[Any]:
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass
    raise _Unreadable(f"{cls.__name__} has no {name}()")


def _scope_for(cls: type[Any], method: str) -> tuple[ast.FunctionDef, _Scope]:
    owner = _owner_of(cls, method)
    fn = _function_ast(owner.__dict__[method])
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assignments.setdefault(node.target.id, []).append(node.value)
    return fn, _Scope(cls=cls, module=sys.modules[owner.__module__], assignments=assignments)


def _sole_return(func_node: ast.FunctionDef) -> ast.expr | None:
    """The one returned expression, or ``None`` if the helper has branches.

    A helper with several returns could produce different key sets or formats
    per branch, so it is treated as unresolvable rather than guessed at.
    """
    returns = [n.value for n in ast.walk(func_node) if isinstance(n, ast.Return) and n.value is not None]
    return returns[0] if len(returns) == 1 else None


def _resolve_helper(call: ast.Call, scope: _Scope) -> tuple[ast.expr, _Scope] | None:
    """Return the single returned expression of a resolvable helper call."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self":
        try:
            node, sub = _scope_for(scope.cls, func.attr)
        except _Unreadable:
            return None
        value = _sole_return(node)
        return (value, sub) if value is not None else None
    if isinstance(func, ast.Name):
        target = getattr(scope.module, func.id, None)
        if not inspect.isfunction(target):
            return None
        try:
            node = _function_ast(target)
        except _Unreadable:
            return None
        sub = _Scope(cls=scope.cls, module=sys.modules[target.__module__], assignments={})
        value = _sole_return(node)
        return (value, sub) if value is not None else None
    return None


def _as_mapping(expr: ast.expr, scope: _Scope, depth: int = 0) -> dict[str, ast.expr]:
    """Resolve ``expr`` to a literal ``{env key: value expression}`` mapping."""
    if depth > _MAX_DEPTH:
        raise _Unreadable("mapping resolution too deep")
    if isinstance(expr, ast.Dict):
        out: dict[str, ast.expr] = {}
        for key, value in zip(expr.keys, expr.values, strict=True):
            if key is None:  # {**other}
                out.update(_as_mapping(value, scope, depth + 1))
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                out[key.value] = value
            else:
                raise _Unreadable(f"non-literal env key {ast.unparse(key)}")
        return out
    if isinstance(expr, ast.Name):
        bound = scope.assignments.get(expr.id)
        if not bound:
            raise _Unreadable(f"unbound env mapping {expr.id}")
        return _as_mapping(bound[-1], scope, depth + 1)
    if isinstance(expr, ast.Call):
        resolved = _resolve_helper(expr, scope)
        if resolved is None:
            raise _Unreadable(f"unresolvable env mapping {ast.unparse(expr.func)}")
        value, sub = resolved
        return _as_mapping(value, sub, depth + 1)
    raise _Unreadable(f"env mapping is a {type(expr).__name__}")


def _binding_env_exprs(cls: type[Any]) -> tuple[dict[str, ast.expr], _Scope]:
    """Map every env key ``binding()`` emits to the expression producing it."""
    fn, scope = _scope_for(cls, "binding")
    calls = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Binding"
    ]
    if not calls:
        if all(isinstance(stmt, ast.Raise) for stmt in _executable_body(fn)):
            raise _NoBinding("binding() unconditionally raises")
        raise _Unreadable("binding() constructs no Binding()")
    env: dict[str, ast.expr] = {}
    holders: set[str] = set()
    for call in calls:
        for keyword in call.keywords:
            if keyword.arg != "env_vars":
                continue
            env.update(_as_mapping(keyword.value, scope))
            if isinstance(keyword.value, ast.Name):
                holders.add(keyword.value.id)
    # Keys added after the dict literal: env_vars["X"] = ... / .update({...})
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Name):
                    continue
                if target.value.id not in holders:
                    continue
                if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                    env[target.slice.value] = node.value
                else:
                    raise _Unreadable(f"computed env key {ast.unparse(target.slice)}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if not isinstance(owner, ast.Name) or owner.id not in holders:
                continue
            if node.func.attr == "update" and node.args:
                env.update(_as_mapping(node.args[0], scope))
    return env, scope


# --------------------------------------------------------------------------
# Value-format classification
# --------------------------------------------------------------------------


def _is_json_container(text: str) -> bool:
    candidate = text.strip()
    if not candidate.startswith(("[", "{")):
        return False
    try:
        json.loads(candidate)
    except ValueError:
        return False
    return True


def _value_format(expr: ast.expr, scope: _Scope, depth: int = 0) -> str:
    """Classify how a binding value is *encoded*, ignoring what it holds.

    The interesting distinctions are the ones that break a consumer parsing the
    value: a JSON array, a delimiter-joined list, a secrets-backend reference,
    or a plain scalar. ``str(x).lower()`` and a literal ``"false"`` are both
    scalars -- they are interchangeable for a reader.
    """
    if depth > _MAX_DEPTH:
        return "scalar"
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        # A hand-written "[]" is still a JSON array; gcp's private-service-connect
        # driver emits exactly that as its empty-NIC-list compatibility value.
        return "json" if _is_json_container(expr.value) else "scalar"
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name) and func.id == "ValueRef":
            keywords = {kw.arg: kw.value for kw in expr.keywords}
            if "literal" in keywords:
                return _value_format(keywords["literal"], scope, depth + 1)
            if "secret_ref" in keywords:
                return "secret_ref"
            if expr.args:
                return _value_format(expr.args[0], scope, depth + 1)
            return "scalar"
        if isinstance(func, ast.Attribute):
            if func.attr == "dumps":
                return "json"
            if func.attr == "join":
                delimiter = func.value.value if isinstance(func.value, ast.Constant) else "?"
                return f"join({delimiter!r})"
        resolved = _resolve_helper(expr, scope)
        if resolved is not None:
            value, sub = resolved
            return _value_format(value, sub, depth + 1)
        return "scalar"
    if isinstance(expr, ast.Name):
        bound = scope.assignments.get(expr.id)
        if not bound:
            return "scalar"
        formats = {_value_format(item, scope, depth + 1) for item in bound}
        return formats.pop() if len(formats) == 1 else "scalar"
    if isinstance(expr, ast.IfExp | ast.BoolOp):
        branches = expr.values if isinstance(expr, ast.BoolOp) else [expr.body, expr.orelse]
        formats = {_value_format(branch, scope, depth + 1) for branch in branches} - {"scalar"}
        return formats.pop() if len(formats) == 1 else "scalar"
    return "scalar"


# --------------------------------------------------------------------------
# Cached extraction shared by the assertions below
# --------------------------------------------------------------------------


def _readable_drivers() -> Iterator[tuple[_Driver, dict[str, ast.expr], _Scope]]:
    """Every driver that actually produces a binding, with its env expressions.

    Ledgered-unreadable and never-binds drivers drop out here; both categories
    are pinned by ``test_every_registered_driver_binding_is_statically_readable``
    so neither can grow unnoticed.
    """
    for driver in DRIVERS:
        if driver.key in _UNREADABLE_BINDINGS:
            continue
        try:
            env, scope = _binding_env_exprs(driver.cls)
        except _NoBinding:
            continue
        yield driver, env, scope


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_reserved_prefixes_cover_every_envelope() -> None:
    """The alias rule has to stay wired to ``_ENVELOPES``, not drift from it."""
    assert set(_RESERVED_PREFIXES) == set(_ENVELOPES), (
        "every kind with an envelope needs a reserved prefix: "
        f"missing={sorted(set(_ENVELOPES) - set(_RESERVED_PREFIXES))} "
        f"stale={sorted(set(_RESERVED_PREFIXES) - set(_ENVELOPES))}"
    )
    for kind, prefixes in sorted(_RESERVED_PREFIXES.items()):
        for key in envelope_keys_for(kind):
            assert key.startswith(prefixes), (
                f"{kind} envelope key {key} is outside its reserved prefixes {prefixes}; "
                "add the prefix so aliases are still distinguishable"
            )


def test_every_registered_driver_binding_is_statically_readable() -> None:
    """No silent skips: an unreadable ``binding()`` must be ledgered."""
    unreadable: dict[tuple[str, str, str], str] = {}
    never_binds: set[tuple[str, str, str]] = set()
    for driver in DRIVERS:
        try:
            _binding_env_exprs(driver.cls)
        except _NoBinding:
            never_binds.add(driver.key)
        except _Unreadable as exc:
            unreadable[driver.key] = str(exc)

    assert set(unreadable) == set(_UNREADABLE_BINDINGS), (
        "the set of drivers whose binding() cannot be read statically changed. "
        f"newly unreadable={ {k: unreadable[k] for k in sorted(set(unreadable) - set(_UNREADABLE_BINDINGS))} } "
        f"now readable (drop the ledger entry)={sorted(set(_UNREADABLE_BINDINGS) - set(unreadable))}"
    )
    assert never_binds == _NEVER_BINDS, (
        "the set of drivers whose binding() unconditionally raises changed: "
        f"new={sorted(never_binds - _NEVER_BINDS)} gone={sorted(_NEVER_BINDS - never_binds)}"
    )


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda d: d.label)
def test_driver_emits_only_envelope_keys_or_provider_aliases(driver: _Driver) -> None:
    """A key wearing the kind's own prefix must be in that kind's envelope.

    Provider-native aliases (``AZURE_FILE_SHARE_NAME``, ``EFS_FILE_SYSTEM_ID``)
    are fine. ``FUNCTION_RESOURCE_ID`` next to the ``faas`` envelope is not: it
    looks portable and is not.
    """
    if driver.key in _UNREADABLE_BINDINGS:
        pytest.xfail(f"binding() not statically readable, tracked in {_UNREADABLE_BINDINGS[driver.key]}")
    prefixes = _RESERVED_PREFIXES.get(driver.kind)
    if prefixes is None:
        pytest.skip(f"kind {driver.kind} has no canonical envelope")
    try:
        env, _ = _binding_env_exprs(driver.cls)
    except _NoBinding:
        return
    canonical = set(envelope_keys_for(driver.kind))
    offenders = frozenset(key for key in env if key.startswith(prefixes) and key not in canonical)

    assert offenders == _UNDECLARED_ENVELOPE_KEYS.get(driver.key, frozenset()), (
        f"{driver.label} emits envelope-prefixed keys that are not in "
        f"_ENVELOPES[{driver.kind!r}]: {sorted(offenders)}. Either rename them to a "
        "provider-local alias, or add them to the envelope and every sibling driver."
    )


@pytest.mark.parametrize("kind", sorted(_ENVELOPES), ids=lambda k: k)
def test_every_driver_for_a_kind_emits_the_same_envelope_subset(kind: str) -> None:
    """#1398's failure mode: one driver quietly omits a key its siblings emit."""
    canonical = set(envelope_keys_for(kind))
    emitted: dict[_Driver, frozenset[str]] = {}
    for driver, env, _ in _readable_drivers():
        if driver.kind == kind:
            emitted[driver] = frozenset(set(env) & canonical)
    if len(emitted) < 2:
        pytest.skip(f"kind {kind} has fewer than two readable drivers")

    union: set[str] = set().union(*emitted.values())
    for driver, keys in sorted(emitted.items(), key=lambda item: item[0].label):
        missing = frozenset(union - keys)
        assert missing == _ENVELOPE_SUBSET_DIVERGENCE.get(driver.key, frozenset()), (
            f"{driver.label} omits envelope keys its siblings emit: {sorted(missing)}. "
            f"Every {kind} driver must emit the same envelope subset, or a consumer that "
            "moves between providers silently loses those variables."
        )


@pytest.mark.parametrize("kind", sorted(_ENVELOPES), ids=lambda k: k)
def test_value_formats_agree_across_drivers_for_the_same_key(kind: str) -> None:
    """Same key, same encoding.

    The ``PRIVATE_ENDPOINT_IPS`` bug was one driver comma-joining a list where
    two siblings emitted a JSON array; both sides parse, only one is right.
    """
    formats: dict[str, dict[str, set[str]]] = {}
    for driver, env, scope in _readable_drivers():
        if driver.kind != kind:
            continue
        for key, expr in env.items():
            formats.setdefault(key, {}).setdefault(_value_format(expr, scope), set()).add(driver.label)

    for key, by_format in sorted(formats.items()):
        if len(by_format) < 2:
            continue
        actual = {name: frozenset(who) for name, who in by_format.items()}
        assert actual == _VALUE_FORMAT_DIVERGENCE.get((kind, key), {}), (
            f"{kind}.{key} is emitted in {len(by_format)} different value formats: "
            + "; ".join(f"{name} by {sorted(who)}" for name, who in sorted(by_format.items()))
            + ". A consumer cannot parse both."
        )
