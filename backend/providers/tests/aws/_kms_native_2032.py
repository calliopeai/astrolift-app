"""Native KMS fixture; correct only Moto's documented multi-Region gaps."""

from __future__ import annotations

import copy
import json
import re
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from moto import mock_aws

from aws.managed.encryption_kms import KMSConfig, KMSDriver

OTHER_ID = "22222222-2222-4222-8222-222222222222"
WRITES = (
    "create_key",
    "create_alias",
    "update_key_description",
    "put_key_policy",
    "enable_key",
    "disable_key",
    "enable_key_rotation",
    "disable_key_rotation",
    "cancel_key_deletion",
    "replicate_key",
    "create_grant",
    "revoke_grant",
    "schedule_key_deletion",
    "import_key_material",
)


def spies(state):
    stack = ExitStack()
    calls = [
        stack.enter_context(patch.object(api, name, wraps=getattr(api, name)))
        for api in state.clients.values()
        for name in WRITES
    ]
    return stack, calls


@contextmanager
def native_kms(monkeypatch):
    from moto.kms import models
    from moto.kms.responses import KmsResponse

    original_id = models.generate_key_id
    original_replication = KmsResponse.replicate_key
    original_validation = KmsResponse._validate_cmk_id

    def validate(response, identity):
        if re.fullmatch(r"mrk-[0-9a-f]{32}", identity) and identity in response.kms_backend.keys:
            return
        original_validation(response, identity)

    def key_id(multi_region=False):
        value = original_id(multi_region)
        return "mrk-" + value.removeprefix("mrk-").replace("-", "") if multi_region else value

    def replicate(response):
        original_replication(response)
        identity, region = response._get_param("KeyId"), response._get_param("ReplicaRegion")
        primary = response.kms_backend.keys[identity]
        backend = models.kms_backends[response.kms_backend.account_id][region]
        replica = backend.keys[identity]
        # Moto shallow-copies primary children, omits requested tags and reports PRIMARY.
        replica.grants, replica.aliases = {}, {}
        replica.multi_region_configuration = copy.deepcopy(primary.multi_region_configuration)
        replica.multi_region_configuration.update(MultiRegionKeyType="REPLICA", ReplicaKeys=[])
        tags = {item["TagKey"]: item["TagValue"] for item in response._get_param("Tags") or []}
        backend.tag_resource(replica.arn, tags)
        replica.description = response._get_param("Description") or ""
        replica.policy = response._get_param("Policy") or replica.generate_default_policy()
        return json.dumps({"ReplicaKeyMetadata": replica.to_dict()["KeyMetadata"], "ReplicaPolicy": replica.policy})

    monkeypatch.setattr(models, "generate_key_id", key_id)
    monkeypatch.setattr(KmsResponse, "replicate_key", replicate)
    monkeypatch.setattr(KmsResponse, "_validate_cmk_id", validate)
    with mock_aws():
        clients = {
            region: boto3.client(
                "kms", region_name=region, aws_access_key_id="fixture", aws_secret_access_key="fixture"
            )
            for region in ("us-east-1", "us-west-2", "eu-west-1")
        }
        cfg = KMSConfig(region="us-east-1", account_id="123456789012", alias_name_prefix="alias/platform")
        state = SimpleNamespace(api=clients[cfg.region], clients=clients, cfg=cfg)
        state.driver = KMSDriver(config=cfg, client=state.api, regional_clients=clients)
        yield state
