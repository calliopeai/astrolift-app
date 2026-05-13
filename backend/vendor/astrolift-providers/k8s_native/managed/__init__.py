"""In-cluster managed-service operator drivers (#53)."""

from k8s_native.managed.postgres_cnpg import CNPGPostgresDriver
from k8s_native.managed.redis_operator import RedisOperatorDriver

__all__ = ["CNPGPostgresDriver", "RedisOperatorDriver"]
