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

Reading branches (#1403)
------------------------
``binding()`` bodies branch. The four Aurora drivers build a completely
different env mapping under ``if self._kind == "postgres"``; every AWS and GCP
Redis driver writes ``REDIS_URL`` twice, once as a secrets reference and once
as a literal; FSx only emits ``FILESYSTEM_USERNAME`` under
``if self.file_system_type == "WINDOWS"``. Reading only the last binding
reported whichever branch happened to be written last, which understated all
three ledgers below -- ``aws/postgres/aurora_postgres`` read as emitting the
MySQL envelope and none of ``POSTGRES_*``.

Every binding of a key is therefore kept, tagged with the ``if`` guards it sits
under. A guard of the form ``self.<attr> == <const>`` is resolved against the
concrete driver class, so statically dead branches drop out and a key left with
no reachable binding is not counted as emitted at all. Anything the guard
resolver cannot decide stays reachable, which keeps the reading conservative.

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
from functools import cache
from typing import TYPE_CHECKING, Any

import pytest
from astrolift_manifest.env_injection import _ENVELOPES, envelope_keys_for

from _sdk import binding_policy
from _sdk.managed_service import Binding, ValueRef
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
    # SES-only refinements of EMAIL_FROM_ADDRESS. azure_acs has no equivalent,
    # so widening the envelope would only move the divergence to #1402.
    ("aws", "email", "ses"): frozenset(
        {
            "EMAIL_FROM_ADDRESS_PREVIEW",
            "EMAIL_FROM_ADDRESS_PRODUCTION",
            "EMAIL_FROM_NAME",
            "EMAIL_REPLY_TO",
            "EMAIL_RETURN_PATH",
        }
    ),
    ("aws", "event_stream", "msk"): frozenset(
        {"EVENT_STREAM_AUTH_MECHANISM", "EVENT_STREAM_CA_CERT", "EVENT_STREAM_CLIENT_CERT", "EVENT_STREAM_CLIENT_KEY"}
    ),
    ("aws", "event_stream", "msk_serverless"): frozenset(
        {"EVENT_STREAM_AUTH_MECHANISM", "EVENT_STREAM_CA_CERT", "EVENT_STREAM_CLIENT_CERT", "EVENT_STREAM_CLIENT_KEY"}
    ),
    ("aws", "filesystem", "efs"): frozenset({"FILESYSTEM_ENDPOINT", "FILESYSTEM_MOUNT_OPTIONS", "FILESYSTEM_PROTOCOL"}),
    # No FILESYSTEM_USERNAME / FILESYSTEM_PASSWORD here: those live under
    # ``if self.file_system_type == "WINDOWS"``, a branch the Lustre and
    # OpenZFS variants never take.
    ("aws", "filesystem", "fsx_lustre"): frozenset(
        {
            "FILESYSTEM_ENDPOINT",
            "FILESYSTEM_MOUNT_OPTIONS",
            "FILESYSTEM_MOUNT_SOURCE",
            "FILESYSTEM_PROTOCOL",
        }
    ),
    ("aws", "filesystem", "fsx_openzfs"): frozenset(
        {
            "FILESYSTEM_ENDPOINT",
            "FILESYSTEM_MOUNT_OPTIONS",
            "FILESYSTEM_MOUNT_SOURCE",
            "FILESYSTEM_PROTOCOL",
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
    ("aws", "mysql", "rds_mysql"): frozenset(
        {"DATABASE_HOST", "DATABASE_NAME", "DATABASE_PASSWORD", "DATABASE_PORT", "DATABASE_USER"}
    ),
    # DNS_NAME duplicates the canonical PRIVATE_ENDPOINT_DNS byte for byte in
    # all three drivers, so it is a redundant name rather than a gap in the
    # envelope; PREFIX_LIST_ID has no Azure Private Link equivalent.
    ("aws", "private_endpoint", "vpc_endpoint"): frozenset(
        {"PRIVATE_ENDPOINT_DNS_NAME", "PRIVATE_ENDPOINT_PREFIX_LIST_ID"}
    ),
    ("aws", "search", "opensearch"): frozenset({"SEARCH_API_KEY", "SEARCH_URL"}),
    ("azure", "api_gateway", "api_management"): frozenset({"API_GATEWAY_PROVIDER"}),
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
    ("azure", "private_endpoint", "private_link"): frozenset({"PRIVATE_ENDPOINT_DNS_NAME", "PRIVATE_ENDPOINT_NAME"}),
    ("azure", "redis", "azure_cache_redis"): frozenset(
        {"REDIS_AUTH_TOKEN", "REDIS_PORT_NON_SSL", "REDIS_SECONDARY_AUTH_TOKEN"}
    ),
    ("azure", "redis", "azure_managed_redis"): frozenset({"REDIS_AUTH_TOKEN", "REDIS_SECONDARY_AUTH_TOKEN"}),
    ("azure", "search", "azure_ai_search_fulltext"): frozenset({"SEARCH_API_KEY", "SEARCH_URL"}),
    ("gcp", "api_gateway", "api_gateway"): frozenset({"API_GATEWAY_HOST"}),
    ("gcp", "cdn", "cloud_cdn"): frozenset({"CDN_DOMAIN", "CDN_IP_ADDRESS", "CDN_URL"}),
    ("gcp", "document_db", "firestore_native"): frozenset({"DOCDB_AUTH_MODE"}),
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
        {"PRIVATE_ENDPOINT_DNS_NAME", "PRIVATE_ENDPOINT_PREFIX_LIST_ID", "PRIVATE_ENDPOINT_URL"}
    ),
    ("gcp", "redis", "memorystore_valkey"): frozenset({"REDIS_CA_CERT", "REDIS_READER_URL"}),
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
}


# Drivers of a kind that do not emit the same envelope subset as their
# siblings. Value is the exact set of envelope keys the driver is missing
# relative to the union emitted across that kind.
# https://github.com/calliopeai/astrolift-app/issues/1402
_ENVELOPE_SUBSET_DIVERGENCE: dict[tuple[str, str, str], frozenset[str]] = {
    ("aws", "model_endpoint", "bedrock"): frozenset({"MODEL_DEPLOYMENT_NAME", "MODEL_REGION"}),
    # Only MASTER_SECRET_REF: the Aurora drivers do emit the rest of the
    # postgres envelope, on the ``self._kind == "postgres"`` branch that the
    # extractor now resolves per subclass.
    ("aws", "postgres", "aurora_postgres"): frozenset({"POSTGRES_MASTER_SECRET_REF"}),
    ("aws", "postgres", "aurora_postgres_serverless_v2"): frozenset({"POSTGRES_MASTER_SECRET_REF"}),
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
    ("azure", "object_store", "azure_blob"): frozenset(
        {"BUCKET_ENDPOINT", "BUCKET_NAME", "BUCKET_PREFIX", "BUCKET_REGION"}
    ),
    ("azure", "object_store", "blob"): frozenset({"BUCKET_ENDPOINT", "BUCKET_NAME", "BUCKET_PREFIX", "BUCKET_REGION"}),
    # Only MASTER_SECRET_REF: just two of the seven postgres drivers publish a
    # master-credential reference, and Azure Flexible Server has no equivalent
    # of a Secrets Manager master secret to point at.
    ("azure", "postgres", "azure_pg_flex"): frozenset({"POSTGRES_MASTER_SECRET_REF"}),
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
    ("k8s_native", "postgres", "cnpg"): frozenset({"POSTGRES_MASTER_SECRET_REF"}),
    ("k8s_native", "queue", "rabbitmq_operator"): frozenset(
        {"QUEUE_ARN_OR_ID", "QUEUE_NAME", "QUEUE_REGION", "QUEUE_URL"}
    ),
    ("k8s_native", "redis", "operator"): frozenset(
        {"REDIS_AUTH_MODE", "REDIS_RESOURCE_ARN", "REDIS_TLS", "REDIS_USER"}
    ),
    ("k8s_native", "vector_index", "opensearch_operator_vector"): frozenset({"VECTOR_NAMESPACE"}),
}


# Keys emitted by more than one driver in disagreeing *encodings* -- a JSON
# array against a comma-joined list, say. A consumer genuinely cannot parse
# both, so this ledger is meant to stay empty.
# https://github.com/calliopeai/astrolift-app/issues/1403
_VALUE_ENCODING_DIVERGENCE: dict[tuple[str, str], dict[str, frozenset[str]]] = {}


# Keys emitted by more than one driver with a disagreeing answer to "is this a
# secret?". Value is the exact mapping of provenance class -> (drivers, why the
# difference is legitimate). Unlike the encoding ledger this one is not
# expected to empty out: whether a value must be referenced rather than inlined
# is a property of the provider, not of the key.
# https://github.com/calliopeai/astrolift-app/issues/1410
_VALUE_PROVENANCE_DIVERGENCE: dict[tuple[str, str], dict[str, tuple[frozenset[str], str]]] = {
    ("document_db", "DOCDB_URI"): {
        "literal": (
            frozenset({"gcp/document_db/firestore_native", "k8s_native/document_db/mongodb_operator"}),
            "Firestore authenticates with IAM and the Percona MongoDB URI names a replica set only, "
            "so neither URI carries a credential to protect.",
        ),
        "secret_ref": (
            frozenset({"aws/document_db/documentdb", "aws/document_db/documentdb_serverless_v2"}),
            "The DocumentDB URI embeds master credentials; inlining it would put them in a "
            "plaintext ManagedServiceBinding column.",
        ),
    },
    ("document_db", "DOCDB_USER"): {
        "literal": (
            frozenset({"aws/document_db/documentdb", "aws/document_db/documentdb_serverless_v2"}),
            "The driver chose the master username itself, so it knows the value.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/document_db/mongodb_operator"}),
            "The Percona operator generates the admin username into its own Secret; the driver "
            "cannot know the value at binding time.",
        ),
    },
    ("filesystem", "FILESYSTEM_USERNAME"): {
        "literal": (
            frozenset({"azure/filesystem/azure_files_classic"}),
            "The Azure Files username is the storage account name, which is an identifier rather "
            "than a credential -- the account key is the secret and rides FILESYSTEM_PASSWORD.",
        ),
        "secret_ref": (
            frozenset({"aws/filesystem/fsx_windows"}),
            "FSx for Windows joins an existing directory, so the mount identity arrives as a "
            "caller-supplied secret reference the driver must pass through untouched.",
        ),
    },
    ("mysql", "MYSQL_USER"): {
        "literal": (
            frozenset(
                {
                    "aws/mysql/aurora_mysql",
                    "aws/mysql/aurora_mysql_serverless_v2",
                    "aws/mysql/rds_mysql",
                    "azure/mysql/azure_mysql_flex",
                    "gcp/mysql/cloudsql",
                }
            ),
            "The driver provisioned the instance and named the master user itself.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/mysql/operator"}),
            "The MySQL operator generates the app username into its own Secret; the driver cannot "
            "know the value at binding time.",
        ),
    },
    ("postgres", "DATABASE_HOST"): {
        "literal": (
            frozenset({"azure/postgres/azure_pg_flex"}),
            "The ARM response carries the FQDN, so the driver knows the value.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    ("postgres", "DATABASE_NAME"): {
        "literal": (
            frozenset({"azure/postgres/azure_pg_flex"}),
            "The driver created the database and named it itself.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    ("postgres", "DATABASE_PORT"): {
        "literal": (
            frozenset({"azure/postgres/azure_pg_flex"}),
            "Azure Flexible Server is always on 5432, so the driver hard-codes it.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    ("postgres", "DATABASE_USER"): {
        "literal": (
            frozenset({"azure/postgres/azure_pg_flex"}),
            "The driver created the administrator login and named it itself.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    # Added by #1402: cnpg now emits the canonical POSTGRES_* names too, so
    # the divergence it already had under the pre-#1003 DATABASE_* aliases
    # shows up under the canonical names as well. Same cause, same reason.
    ("postgres", "POSTGRES_DB"): {
        "literal": (
            frozenset(
                {
                    "aws/postgres/aurora_postgres",
                    "aws/postgres/aurora_postgres_serverless_v2",
                    "aws/postgres/rds",
                    "azure/postgres/azure_pg_flex",
                    "gcp/postgres/alloydb",
                    "gcp/postgres/cloudsql",
                }
            ),
            "The driver created the database and knows its name.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    ("postgres", "POSTGRES_HOST"): {
        "literal": (
            frozenset(
                {
                    "aws/postgres/aurora_postgres",
                    "aws/postgres/aurora_postgres_serverless_v2",
                    "aws/postgres/rds",
                    "azure/postgres/azure_pg_flex",
                    "gcp/postgres/alloydb",
                    "gcp/postgres/cloudsql",
                }
            ),
            "The provisioning response carries the endpoint, so the driver knows it.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    ("postgres", "POSTGRES_PORT"): {
        "literal": (
            frozenset(
                {
                    "aws/postgres/aurora_postgres",
                    "aws/postgres/aurora_postgres_serverless_v2",
                    "aws/postgres/rds",
                    "azure/postgres/azure_pg_flex",
                    "gcp/postgres/alloydb",
                    "gcp/postgres/cloudsql",
                }
            ),
            "A managed Postgres endpoint is on a port the driver already knows.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    ("postgres", "POSTGRES_USER"): {
        "literal": (
            frozenset(
                {
                    "aws/postgres/aurora_postgres",
                    "aws/postgres/aurora_postgres_serverless_v2",
                    "aws/postgres/rds",
                    "azure/postgres/azure_pg_flex",
                    "gcp/postgres/alloydb",
                    "gcp/postgres/cloudsql",
                }
            ),
            "The driver created the master role and named it itself.",
        ),
        "secret_ref": (
            frozenset({"k8s_native/postgres/cnpg"}),
            "CNPG owns the generated -app Secret and rotates it; reading through the reference is "
            "what keeps the binding correct across a rotation.",
        ),
    },
    ("redis", "REDIS_URL"): {
        "conditional": (
            frozenset(
                {
                    "aws/redis/elasticache",
                    "aws/redis/elasticache_serverless_redis",
                    "aws/redis/elasticache_serverless_valkey",
                    "aws/redis/elasticache_valkey",
                    "aws/redis/memorydb",
                    "gcp/redis/memorystore",
                    "gcp/redis/memorystore_valkey",
                }
            ),
            "These drivers already follow the platform rule: a reference when the URL embeds an "
            "auth token, a literal when the instance has no credential to embed.",
        ),
        "literal": (
            frozenset({"k8s_native/redis/operator"}),
            "The in-cluster Redis is reached over the pod network with no auth token, so the URL "
            "has no credential in it.",
        ),
        "secret_ref": (
            frozenset({"azure/redis/azure_cache_redis", "azure/redis/azure_managed_redis"}),
            "Azure Cache for Redis has no keyless mode, so the URL always embeds an access key and "
            "the reference branch is the only one reachable.",
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

_MISSING = object()


@dataclass(frozen=True)
class _Bound:
    """One expression bound to a name, plus the ``if`` guards it sits under.

    ``binding()`` bodies routinely bind the env mapping (or a single key)
    more than once -- ``if self._kind == "postgres": env = {...} else: env =
    {...}``, or ``env["REDIS_URL"] = ValueRef(secret_ref=...)`` on the
    authenticated branch and a literal on the other. Reading only the last
    binding reports whichever branch happens to be written last, so the
    guards are carried alongside the expression and resolved per driver.
    """

    value: ast.expr
    guards: tuple[tuple[ast.expr, bool], ...] = ()

    @property
    def lineno(self) -> int:
        return getattr(self.value, "lineno", 0)


@dataclass(frozen=True)
class _Scope:
    """Everything needed to resolve a name or call inside one function body."""

    cls: type[Any]
    module: Any
    assignments: dict[str, list[_Bound]]


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


def _walk_statements(
    body: list[ast.stmt],
    guards: tuple[tuple[ast.expr, bool], ...],
) -> Iterator[tuple[ast.stmt, tuple[tuple[ast.expr, bool], ...]]]:
    """Yield every statement in ``body`` with the ``if`` guards enclosing it."""
    for node in body:
        if isinstance(node, ast.If):
            yield from _walk_statements(node.body, (*guards, (node.test, True)))
            yield from _walk_statements(node.orelse, (*guards, (node.test, False)))
            continue
        yield node, guards
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
                yield from _walk_statements(value, guards)


def _scope_for(cls: type[Any], method: str) -> tuple[ast.FunctionDef, _Scope]:
    owner = _owner_of(cls, method)
    fn = _function_ast(owner.__dict__[method])
    assignments: dict[str, list[_Bound]] = {}
    for node, guards in _walk_statements(fn.body, ()):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(_Bound(node.value, guards))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assignments.setdefault(node.target.id, []).append(_Bound(node.value, guards))
    return fn, _Scope(cls=cls, module=sys.modules[owner.__module__], assignments=assignments)


def _static_self_value(cls: type[Any], attr: str, depth: int = 0) -> Any:
    """Resolve ``self.<attr>`` to a class-level constant on ``cls``.

    Follows a trivial ``@property`` that only forwards to another attribute,
    which is how the Aurora drivers expose their portable kind
    (``_kind`` -> ``_portable_kind``). Anything with a real body is left
    unresolved rather than guessed at.
    """
    if depth > 2:
        return _MISSING
    value = inspect.getattr_static(cls, attr, _MISSING)
    if not isinstance(value, property):
        return value
    if value.fget is None:
        return _MISSING
    try:
        returned = _sole_return(_function_ast(value.fget))
    except _Unreadable:
        return _MISSING
    if isinstance(returned, ast.Attribute) and isinstance(returned.value, ast.Name) and returned.value.id == "self":
        return _static_self_value(cls, returned.attr, depth + 1)
    return _MISSING


def _decide_guard(test: ast.expr, cls: type[Any]) -> bool | None:
    """Resolve ``self.<attr> == <const>`` against ``cls``; ``None`` if unknowable.

    The four Aurora drivers share one ``binding()`` that branches on
    ``self._kind``, a class attribute of the concrete subclass. Without this
    the postgres variants read as emitting the MySQL envelope and none of
    ``POSTGRES_*``. Everything else stays undecided, which keeps the reading
    conservative: an undecided branch is treated as reachable.
    """
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return None
    left, op, right = test.left, test.ops[0], test.comparators[0]
    if not (isinstance(left, ast.Attribute) and isinstance(left.value, ast.Name) and left.value.id == "self"):
        return None
    if not isinstance(right, ast.Constant):
        return None
    actual = _static_self_value(cls, left.attr)
    if not isinstance(actual, str | int | bool):
        return None
    if isinstance(op, ast.Eq):
        return actual == right.value
    if isinstance(op, ast.NotEq):
        return actual != right.value
    return None


def _is_reachable(bound: _Bound, cls: type[Any]) -> bool:
    return all(_decide_guard(test, cls) in (None, taken) for test, taken in bound.guards)


def _surviving(bounds: list[_Bound], cls: type[Any]) -> list[_Bound]:
    """The bindings a driver can actually emit, in source order.

    An *unguarded* binding overwrites everything written before it, so only
    the bindings from the last unguarded one onwards survive. Guarded
    bindings after that point are alternatives, and all of them are kept.
    """
    live = sorted((b for b in bounds if _is_reachable(b, cls)), key=lambda b: b.lineno)
    last_unconditional = max((i for i, b in enumerate(live) if not b.guards), default=0)
    return live[last_unconditional:]


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


def _merge(into: dict[str, list[_Bound]], other: dict[str, list[_Bound]]) -> None:
    for key, bounds in other.items():
        into.setdefault(key, []).extend(bounds)


def _as_mapping(
    expr: ast.expr,
    scope: _Scope,
    depth: int = 0,
    guards: tuple[tuple[ast.expr, bool], ...] = (),
) -> dict[str, list[_Bound]]:
    """Resolve ``expr`` to ``{env key: [every expression that can produce it]}``.

    A key has more than one entry when the driver binds it on separate
    branches -- ``ValueRef(secret_ref=...)`` when there is a credential to
    reference and ``ValueRef(literal=...)`` when there is not, say.
    """
    if depth > _MAX_DEPTH:
        raise _Unreadable("mapping resolution too deep")
    if isinstance(expr, ast.Dict):
        out: dict[str, list[_Bound]] = {}
        for key, value in zip(expr.keys, expr.values, strict=True):
            if key is None:  # {**other}
                _merge(out, _as_mapping(value, scope, depth + 1, guards))
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                # An explicit key inside one literal is that literal's final
                # word; it overrides anything a preceding spread contributed.
                out[key.value] = [_Bound(value, guards)]
            else:
                raise _Unreadable(f"non-literal env key {ast.unparse(key)}")
        return out
    if isinstance(expr, ast.Name):
        bound = scope.assignments.get(expr.id)
        if not bound:
            raise _Unreadable(f"unbound env mapping {expr.id}")
        surviving = _surviving(bound, scope.cls)
        if not surviving:
            raise _Unreadable(f"no reachable binding for env mapping {expr.id}")
        out = {}
        for item in surviving:
            _merge(out, _as_mapping(item.value, scope, depth + 1, (*guards, *item.guards)))
        return out
    if isinstance(expr, ast.Call):
        resolved = _resolve_helper(expr, scope)
        if resolved is None:
            raise _Unreadable(f"unresolvable env mapping {ast.unparse(expr.func)}")
        value, sub = resolved
        return _as_mapping(value, sub, depth + 1, guards)
    raise _Unreadable(f"env mapping is a {type(expr).__name__}")


def _binding_env_exprs(cls: type[Any]) -> tuple[dict[str, tuple[ast.expr, ...]], _Scope]:
    """Map every env key ``binding()`` emits to the expressions producing it."""
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
    env: dict[str, list[_Bound]] = {}
    holders: set[str] = set()
    for call in calls:
        for keyword in call.keywords:
            if keyword.arg != "env_vars":
                continue
            _merge(env, _as_mapping(keyword.value, scope))
            if isinstance(keyword.value, ast.Name):
                holders.add(keyword.value.id)
    # Keys added after the dict literal: env_vars["X"] = ... / .update({...})
    for node, guards in _walk_statements(fn.body, ()):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Name):
                    continue
                if target.value.id not in holders:
                    continue
                if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                    env.setdefault(target.slice.value, []).append(_Bound(node.value, guards))
                else:
                    raise _Unreadable(f"computed env key {ast.unparse(target.slice)}")
            continue
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute):
            continue
        owner = call.func.value
        if not isinstance(owner, ast.Name) or owner.id not in holders:
            continue
        if call.func.attr == "update" and call.args:
            _merge(env, _as_mapping(call.args[0], scope, guards=guards))
    # A key whose every binding sits on a statically dead branch is not emitted
    # by this driver at all -- ``FILESYSTEM_PASSWORD`` lives under
    # ``if self.file_system_type == "WINDOWS"``, so the Lustre and OpenZFS
    # variants never produce it.
    emitted = ((key, tuple(b.value for b in _surviving(bounds, cls))) for key, bounds in env.items())
    return {key: exprs for key, exprs in emitted if exprs}, scope


# --------------------------------------------------------------------------
# Value classification: encoding and provenance are separate questions
# --------------------------------------------------------------------------
#
# The original single classifier lumped ``secret_ref`` in with ``json`` and
# ``join(',')``, which conflates two unrelated properties and produced nine of
# the ten rows on #1403.
#
# * **Encoding** is what a consumer has to parse. A JSON array and a
#   comma-joined list of the same data are incompatible; that is the
#   ``PRIVATE_ENDPOINT_IPS`` bug and it must not differ between siblings.
# * **Provenance** is where the value comes from. ``managed_service_lifecycle``
#   turns ``ValueRef.secret_ref`` into ``ManagedServiceBinding.is_secret``, and
#   ``app_lifecycle`` resolves the reference back to its raw value before the
#   bindings Secret is written -- so the workload sees the same flat string
#   whichever way the driver emitted it. It still matters (a credential held as
#   a literal sits in a plaintext column, and an unresolvable reference fails
#   the deploy), but it is not a parse hazard.
#
# A ``secret_ref`` therefore has no *knowable* encoding: the guardrail cannot
# see inside the secret. It reports ``_OPAQUE`` and takes no part in the
# encoding comparison.

_OPAQUE = "opaque"


def _is_json_container(text: str) -> bool:
    candidate = text.strip()
    if not candidate.startswith(("[", "{")):
        return False
    try:
        json.loads(candidate)
    except ValueError:
        return False
    return True


def _value_encoding(expr: ast.expr, scope: _Scope, depth: int = 0) -> str:
    """Classify how a binding value is *encoded*, ignoring what it holds.

    ``str(x).lower()`` and a literal ``"false"`` are both scalars -- they are
    interchangeable for a reader. A secrets-backend reference is ``_OPAQUE``.
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
                return _value_encoding(keywords["literal"], scope, depth + 1)
            if "secret_ref" in keywords:
                return _OPAQUE
            if expr.args:
                return _value_encoding(expr.args[0], scope, depth + 1)
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
            return _value_encoding(value, sub, depth + 1)
        return "scalar"
    if isinstance(expr, ast.Name):
        bound = scope.assignments.get(expr.id)
        if not bound:
            return "scalar"
        formats = {_value_encoding(item.value, scope, depth + 1) for item in _surviving(bound, scope.cls)}
        return formats.pop() if len(formats) == 1 else "scalar"
    if isinstance(expr, ast.IfExp | ast.BoolOp):
        branches = expr.values if isinstance(expr, ast.BoolOp) else [expr.body, expr.orelse]
        formats = {_value_encoding(branch, scope, depth + 1) for branch in branches} - {"scalar"}
        return formats.pop() if len(formats) == 1 else "scalar"
    return "scalar"


def _value_provenance(expr: ast.expr, scope: _Scope, depth: int = 0) -> str:
    """``secret_ref`` if the value is a secrets-backend reference, else ``literal``."""
    if depth > _MAX_DEPTH:
        return "literal"
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name) and func.id == "ValueRef":
            keywords = {kw.arg: kw.value for kw in expr.keywords}
            if "secret_ref" in keywords:
                return "secret_ref"
            if "literal" in keywords or expr.args:
                return "literal"
            return "literal"
        resolved = _resolve_helper(expr, scope)
        if resolved is not None:
            value, sub = resolved
            return _value_provenance(value, sub, depth + 1)
    if isinstance(expr, ast.Name):
        bound = scope.assignments.get(expr.id)
        if bound:
            kinds = {_value_provenance(item.value, scope, depth + 1) for item in _surviving(bound, scope.cls)}
            if len(kinds) == 1:
                return kinds.pop()
    if isinstance(expr, ast.IfExp):
        kinds = {_value_provenance(branch, scope, depth + 1) for branch in (expr.body, expr.orelse)}
        if len(kinds) == 1:
            return kinds.pop()
        return "conditional"
    return "literal"


def _driver_provenance(exprs: tuple[ast.expr, ...], scope: _Scope) -> str:
    """One label per driver per key: ``literal``, ``secret_ref`` or ``conditional``.

    ``conditional`` is the honest reading of every AWS and GCP Redis driver:
    ``REDIS_URL`` is a reference when the URL embeds a credential and a literal
    when it does not.
    """
    kinds = {_value_provenance(expr, scope) for expr in exprs}
    if kinds == {"literal"}:
        return "literal"
    if kinds == {"secret_ref"}:
        return "secret_ref"
    return "conditional"


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


_Divergence = dict[tuple[str, str], dict[str, frozenset[str]]]


@cache
def _value_divergences() -> tuple[_Divergence, _Divergence]:
    """``(encoding, provenance)`` divergences keyed by ``(kind, env key)``.

    Only keys more than one driver disagrees about are returned, which is
    exactly what the two ledgers hold.
    """
    encodings: dict[tuple[str, str], dict[str, set[str]]] = {}
    provenance: dict[tuple[str, str], dict[str, set[str]]] = {}
    for driver, env, scope in _readable_drivers():
        if driver.kind not in _ENVELOPES:
            continue
        for key, exprs in env.items():
            for encoding in {_value_encoding(expr, scope) for expr in exprs} - {_OPAQUE}:
                encodings.setdefault((driver.kind, key), {}).setdefault(encoding, set()).add(driver.label)
            provenance.setdefault((driver.kind, key), {}).setdefault(_driver_provenance(exprs, scope), set()).add(
                driver.label
            )

    def _divergent(source: dict[tuple[str, str], dict[str, set[str]]]) -> _Divergence:
        return {
            subject: {name: frozenset(who) for name, who in by_class.items()}
            for subject, by_class in source.items()
            if len(by_class) > 1
        }

    return _divergent(encodings), _divergent(provenance)


@pytest.mark.parametrize("kind", sorted(_ENVELOPES), ids=lambda k: k)
def test_value_encodings_agree_across_drivers_for_the_same_key(kind: str) -> None:
    """Same key, same encoding.

    The ``PRIVATE_ENDPOINT_IPS`` bug was one driver comma-joining a list where
    two siblings emitted a JSON array; both sides parse, only one is right.
    Secrets-backend references are ``_OPAQUE`` and sit this comparison out --
    see the note above ``_value_encoding``.
    """
    encodings, _ = _value_divergences()
    for (subject_kind, key), by_encoding in sorted(encodings.items()):
        if subject_kind != kind:
            continue
        assert by_encoding == _VALUE_ENCODING_DIVERGENCE.get((kind, key), {}), (
            f"{kind}.{key} is emitted in {len(by_encoding)} different encodings: "
            + "; ".join(f"{name} by {sorted(who)}" for name, who in sorted(by_encoding.items()))
            + ". A consumer cannot parse both."
        )


@pytest.mark.parametrize("kind", sorted(_ENVELOPES), ids=lambda k: k)
def test_value_provenance_agrees_across_drivers_for_the_same_key(kind: str) -> None:
    """Same key, same answer to "is this a secret?".

    Not a parse hazard -- ``app_lifecycle`` resolves a reference to its raw
    value before the workload sees it -- but it decides whether the value is
    stored in a plaintext column, whether ``agent_secrets`` routes it as a
    reference, and whether a missing secret fails the deploy. Every entry in
    the ledger names why the two sides legitimately differ.
    """
    _, provenance = _value_divergences()
    for (subject_kind, key), by_provenance in sorted(provenance.items()):
        if subject_kind != kind:
            continue
        expected = {name: who for name, (who, _) in _VALUE_PROVENANCE_DIVERGENCE.get((kind, key), {}).items()}
        assert by_provenance == expected, (
            f"{kind}.{key} disagrees on provenance across drivers: "
            + "; ".join(f"{name} by {sorted(who)}" for name, who in sorted(by_provenance.items()))
            + ". Ledger it with the reason, or make the drivers agree."
        )


def test_no_stale_value_ledger_entries() -> None:
    """The two value ledgers ratchet down, so a repaired key must leave them.

    The per-kind assertions above only run for keys that *are* divergent, so
    without this an entry for a key the drivers now agree on would sit there
    unnoticed and mask the next regression on that key.
    """
    encodings, provenance = _value_divergences()

    assert set(_VALUE_ENCODING_DIVERGENCE) == set(encodings), (
        "stale encoding ledger entries (drivers now agree, drop them)="
        f"{sorted(set(_VALUE_ENCODING_DIVERGENCE) - set(encodings))} "
        f"unledgered={sorted(set(encodings) - set(_VALUE_ENCODING_DIVERGENCE))}"
    )
    assert set(_VALUE_PROVENANCE_DIVERGENCE) == set(provenance), (
        "stale provenance ledger entries (drivers now agree, drop them)="
        f"{sorted(set(_VALUE_PROVENANCE_DIVERGENCE) - set(provenance))} "
        f"unledgered={sorted(set(provenance) - set(_VALUE_PROVENANCE_DIVERGENCE))}"
    )


def test_every_provenance_ledger_entry_carries_a_reason() -> None:
    """A ledger row without a stated reason is just a skip with extra steps."""
    for (kind, key), by_provenance in sorted(_VALUE_PROVENANCE_DIVERGENCE.items()):
        for name, (who, reason) in sorted(by_provenance.items()):
            assert who, f"{kind}.{key} provenance {name!r} lists no driver"
            assert len(reason) > 20, f"{kind}.{key} provenance {name!r} needs a real reason, got {reason!r}"


# --------------------------------------------------------------------------
# The extractor's own contract
# --------------------------------------------------------------------------
#
# Everything above is only as trustworthy as the reading below it, and the
# branch handling is the subtle part: it decides whether
# ``aws/postgres/aurora_postgres`` is read as emitting the postgres envelope or
# the MySQL one. These pin it against hand-written bindings whose right answer
# is obvious by inspection.


class _BranchingBinding:
    """Stand-in for the shape every Aurora driver has."""

    _portable_kind = "unset"

    @property
    def _kind(self) -> str:
        return self._portable_kind

    def binding(self) -> Binding:
        if self._kind == "postgres":
            env = {"POSTGRES_HOST": ValueRef(literal="pg-host")}
        else:
            env = {"MYSQL_HOST": ValueRef(literal="my-host")}
        return Binding(env_vars=env)


class _PostgresFlavour(_BranchingBinding):
    _portable_kind = "postgres"


class _MySQLFlavour(_BranchingBinding):
    _portable_kind = "mysql"


class _UndecidableFlavour(_BranchingBinding):
    """No class-level ``_portable_kind`` value the reader can resolve."""

    @property
    def _kind(self) -> str:
        return "postgres" if self.binding else "mysql"


class _AlternatingValue:
    """The Redis shape: one key, a reference on one branch and a literal on the other."""

    authenticated = True

    def binding(self) -> Binding:
        env = {"REDIS_HOST": ValueRef(literal="host")}
        if self.authenticated:
            env["REDIS_URL"] = ValueRef(secret_ref="vault://url")
        else:
            env["REDIS_URL"] = ValueRef(literal="redis://host:6379")
        return Binding(env_vars=env)


class _OverwrittenValue:
    """An unguarded write after a guarded one wins outright."""

    flag = True

    def binding(self) -> Binding:
        env = {"CACHE_NODES": ValueRef(literal="first")}
        if self.flag:
            env["CACHE_NODES"] = ValueRef(secret_ref="vault://second")
        env["CACHE_NODES"] = ValueRef(literal=",".join(["third"]))
        return Binding(env_vars=env)


class _WindowsOnlyKey:
    """FSx's shape: a key only reachable for one concrete variant."""

    file_system_type = "LUSTRE"

    def binding(self) -> Binding:
        env = {"FILESYSTEM_HANDLE": ValueRef(literal="fs-1")}
        if self.file_system_type == "WINDOWS":
            env["FILESYSTEM_USERNAME"] = ValueRef(secret_ref="vault://user")
        return Binding(env_vars=env)


class _WindowsVariant(_WindowsOnlyKey):
    file_system_type = "WINDOWS"


def test_static_branch_on_a_class_attribute_picks_the_live_mapping() -> None:
    postgres, _ = _binding_env_exprs(_PostgresFlavour)
    mysql, _ = _binding_env_exprs(_MySQLFlavour)

    assert set(postgres) == {"POSTGRES_HOST"}
    assert set(mysql) == {"MYSQL_HOST"}


def test_an_undecidable_branch_keeps_both_mappings() -> None:
    """Conservative by design: an unreadable guard must not drop real keys."""
    env, _ = _binding_env_exprs(_UndecidableFlavour)

    assert set(env) == {"POSTGRES_HOST", "MYSQL_HOST"}


def test_a_key_bound_on_both_branches_keeps_both_expressions() -> None:
    env, scope = _binding_env_exprs(_AlternatingValue)

    assert len(env["REDIS_URL"]) == 2
    assert _driver_provenance(env["REDIS_URL"], scope) == "conditional"
    assert _driver_provenance(env["REDIS_HOST"], scope) == "literal"


def test_an_unguarded_rebind_discards_everything_written_before_it() -> None:
    env, scope = _binding_env_exprs(_OverwrittenValue)

    assert len(env["CACHE_NODES"]) == 1
    assert _driver_provenance(env["CACHE_NODES"], scope) == "literal"
    assert _value_encoding(env["CACHE_NODES"][0], scope) == "join(',')"


def test_a_key_only_on_a_dead_branch_is_not_emitted() -> None:
    lustre, _ = _binding_env_exprs(_WindowsOnlyKey)
    windows, _ = _binding_env_exprs(_WindowsVariant)

    assert "FILESYSTEM_USERNAME" not in lustre
    assert "FILESYSTEM_USERNAME" in windows


def test_a_secret_reference_has_no_knowable_encoding() -> None:
    """Otherwise provenance leaks into the encoding comparison, which is #1403."""
    env, scope = _binding_env_exprs(_AlternatingValue)
    encodings = {_value_encoding(expr, scope) for expr in env["REDIS_URL"]}

    assert encodings == {_OPAQUE, "scalar"}


@pytest.mark.parametrize("kind", sorted(_ENVELOPES), ids=lambda k: k)
def test_every_binding_value_follows_the_provenance_rule(kind: str) -> None:
    """Not just the keys drivers disagree about (#1410).

    The divergence ledger above only sees a key when two drivers answer
    differently. A single driver inlining a credential, or referencing a port,
    is wrong on its own and passes that check unnoticed because nobody
    contradicts it.

    ``_sdk.binding_policy`` states the rule the ledger was an unwritten
    approximation of, so this applies it to every emission.
    """
    offences: list[str] = []
    for driver, env, scope in _readable_drivers():
        if driver.kind != kind:
            continue
        for key, exprs in env.items():
            provenance = _driver_provenance(exprs, scope)
            if provenance not in (binding_policy.LITERAL, binding_policy.SECRET_REF):
                # ``conditional`` means the driver emits both depending on a
                # branch, which is the correct shape for a connection string
                # whose credential content depends on configuration.
                continue
            message = binding_policy.violation(key, driver.label, provenance)
            if message:
                offences.append(message)

    assert not offences, "\n".join(offences)
