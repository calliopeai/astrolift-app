"""Driver binding() must emit the canonical connection envelope (#1003).

The deploy render materializes ``ManagedServiceBinding`` rows from
``driver.binding()`` into the per-app bindings Secret, and apps read the
keys defined in ``astrolift_manifest.env_injection._ENVELOPES``. If a
driver emits driver-local key names (the old ``DATABASE_*`` /
``REDIS_AUTH_TOKEN`` / ``S3_BUCKET_*``) the injected env won't match what
apps read and every binding silently no-ops. These tests pin the
contract: each driver's binding() keys must be a superset of the
canonical envelope for its kind.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from _sdk.managed_service import ServiceHandle
from astrolift_manifest.env_injection import envelope_keys_for


def test_s3_binding_emits_canonical_object_store_envelope():
    from aws.managed.object_store_s3 import S3Config, S3Driver

    drv = S3Driver(config=S3Config(region="us-west-2"), client=MagicMock())
    binding = drv.binding(ServiceHandle(handle="object_store/astrolift-acme-bucket"))
    keys = set(binding.env_vars)
    assert set(envelope_keys_for("object_store")) <= keys, keys
    assert binding.env_vars["BUCKET_NAME"].literal == "astrolift-acme-bucket"
    assert binding.env_vars["BUCKET_REGION"].literal == "us-west-2"


def test_postgres_binding_emits_canonical_postgres_envelope():
    from aws.managed.postgres_rds import RDSConfig, RDSPostgresDriver

    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{
            "DBInstanceStatus": "available",
            "Endpoint": {"Address": "db.example.rds.amazonaws.com", "Port": 5432},
            "DBName": "acme_prod",
            "MasterUsername": "astrolift",
        }],
    }
    sm = MagicMock()
    sm.get_secret_value.return_value = {"SecretString": "s3cr3t/p@ss"}
    drv = RDSPostgresDriver(
        config=RDSConfig(region="us-west-2", db_subnet_group="g", security_group_ids=["sg-1"]),
        rds_client=rds,
        secrets_client=sm,
    )
    binding = drv.binding(ServiceHandle(handle="rds/astrolift-acme-prod-records"))
    keys = set(binding.env_vars)
    # Every canonical postgres key (minus the optional master-secret-ref) present.
    canonical = set(envelope_keys_for("postgres")) - {"POSTGRES_MASTER_SECRET_REF"}
    assert canonical <= keys, keys
    assert binding.env_vars["POSTGRES_PASSWORD"].secret_ref  # password is a secret ref
    assert binding.env_vars["POSTGRES_HOST"].literal == "db.example.rds.amazonaws.com"
    # binding() must materialize the DATABASE_URL secret it references (#1009
    # followup) — otherwise the secret-ref is unresolvable and the bindings
    # Secret fails to build. URL must be a real postgres DSN with the password
    # URL-encoded (the / in the password becomes %2F).
    sm.create_secret.assert_called_once()
    url = sm.create_secret.call_args.kwargs["SecretString"]
    assert url.startswith("postgresql://astrolift:s3cr3t%2Fp%40ss@db.example.rds.amazonaws.com:5432/")
    assert "sslmode=require" in url


def test_redis_binding_emits_canonical_redis_envelope():
    from aws.managed.redis_elasticache import ElastiCacheConfig, ElastiCacheRedisDriver

    ec = MagicMock()
    ec.describe_replication_groups.return_value = {
        "ReplicationGroups": [{
            "Status": "available",
            "TransitEncryptionEnabled": True,
            "NodeGroups": [{"PrimaryEndpoint": {"Address": "redis.example.cache.amazonaws.com", "Port": 6379}}],
        }],
    }
    drv = ElastiCacheRedisDriver(
        config=ElastiCacheConfig(region="us-west-2", cache_subnet_group="g", security_group_ids=["sg-1"]),
        elasticache_client=ec,
        secrets_client=MagicMock(),
    )
    binding = drv.binding(ServiceHandle(handle="redis/astrolift-acme-prod-cache"))
    keys = set(binding.env_vars)
    assert {"REDIS_HOST", "REDIS_PORT", "REDIS_USER", "REDIS_PASSWORD", "REDIS_TLS"} <= keys, keys
    assert "REDIS_AUTH_TOKEN" not in keys  # renamed to canonical REDIS_PASSWORD
