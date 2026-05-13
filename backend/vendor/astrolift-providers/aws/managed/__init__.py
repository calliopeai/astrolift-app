"""AWS managed-service drivers (#35).

Per (kind, variant) driver implementations registered via the
PLUGIN manifest's `managed_service_drivers` map.

Currently shipped: object_store/s3, queue/sqs.
Pending: postgres/rds, postgres/aurora, redis/elasticache,
nosql/dynamodb, pubsub/sns, filesystem/efs.
"""

from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)

__all__ = [
    "ManagedServiceError",
    "handle_for",
    "parse_handle",
    "tags_for",
]
