"""
Abstract managed service catalog (#11, spec 11 §3).

Maps the 16 abstract service kinds to their canonical descriptions:

  - human-readable purpose,
  - the connection envelope shape (delegates to env_injection),
  - the set of provider variants the platform plugins publish.

Pure-Python registry. Plugins register their concrete variants at
import time; the catalog merges them under the abstract kind so
the GraphQL ``catalog`` query can list 'every postgres flavor we
can deploy on this install'.

The 16 kinds exactly match spec 11 §6 — adding a new one means
both this catalog and the env_injection envelope grow together
(lock-tested).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from astrolift_manifest.env_injection import envelope_keys_for

# Per spec 11 §3, every abstract kind has a stable description. The
# UI's catalog page reads this; the manifest validator verifies the
# user's [[managed_services]] kind is one of these.
KIND_DESCRIPTIONS: dict[str, str] = {
    "postgres": "Relational database (PostgreSQL wire protocol).",
    "mysql": "Relational database (MySQL wire protocol).",
    "redis": "In-memory key-value store with pub/sub + Streams.",
    "mq": "Kafka-protocol durable message broker.",
    "queue": "FIFO/standard message queue (SQS, Pub/Sub queue, etc).",
    "topic": "Publish/subscribe topic (SNS, Pub/Sub topic).",
    "kv_store": "Wide-column / partitioned key-value store (DynamoDB-shape).",
    "document_db": "Document store (MongoDB-shape; CosmosDB, DocumentDB).",
    "search": "Full-text search engine (Elasticsearch-compatible).",
    "vector_index": "Vector similarity index (Pinecone, Weaviate, pgvector).",
    "time_series": "Time-series database (InfluxDB-shape, Mimir).",
    "object_store": "Blob storage with HTTP API (S3, GCS, Blob).",
    "nfs": "Shared filesystem mounted into pods.",
    "cdn": "Content delivery network for static assets.",
    "email": "Transactional email provider.",
    "sms": "Transactional SMS / messaging provider.",
}

ALL_KINDS: tuple[str, ...] = tuple(KIND_DESCRIPTIONS)


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogVariant:
    """One concrete variant published by a plugin."""

    plugin_slug: str
    variant: str
    kind: str
    is_default_for_kind: bool = False
    description: str = ""

    @property
    def fqn(self) -> str:
        return f"{self.plugin_slug}/{self.variant}"


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One kind's entry: description + envelope keys + registered
    variants. The UI catalog page reads this directly."""

    kind: str
    description: str
    envelope_keys: tuple[str, ...]
    variants: tuple[CatalogVariant, ...]


_VARIANTS: dict[str, list[CatalogVariant]] = {k: [] for k in ALL_KINDS}


def register_variant(variant: CatalogVariant) -> None:
    """Plugin packages call this at import time."""
    if variant.kind not in KIND_DESCRIPTIONS:
        raise ValueError(
            f"variant {variant.fqn!r} declares kind {variant.kind!r} "
            f"which is not in the abstract catalog "
            f"(known: {sorted(ALL_KINDS)})"
        )
    _VARIANTS[variant.kind].append(variant)


def register_variants(variants: Iterable[CatalogVariant]) -> None:
    for v in variants:
        register_variant(v)


def clear_catalog() -> None:
    """Test helper."""
    for k in _VARIANTS:
        _VARIANTS[k] = []


def get_entry(kind: str) -> CatalogEntry:
    """Return the entry for a kind. Raises ``KeyError`` for unknown
    kinds — callers should validate upstream against ``ALL_KINDS``."""
    if kind not in KIND_DESCRIPTIONS:
        raise KeyError(f"unknown service kind {kind!r}")
    return CatalogEntry(
        kind=kind,
        description=KIND_DESCRIPTIONS[kind],
        envelope_keys=envelope_keys_for(kind),
        variants=tuple(_VARIANTS[kind]),
    )


def list_entries() -> tuple[CatalogEntry, ...]:
    """Every kind's entry. Order matches ``ALL_KINDS`` (declaration
    order) so the UI lists them deterministically."""
    return tuple(get_entry(k) for k in ALL_KINDS)


def find_variant(*, plugin_slug: str, variant: str) -> CatalogVariant | None:
    """Lookup helper for the manifest validator."""
    for variants in _VARIANTS.values():
        for v in variants:
            if v.plugin_slug == plugin_slug and v.variant == variant:
                return v
    return None
