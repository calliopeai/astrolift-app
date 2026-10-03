"""Actual boto3/Moto Lambda, IAM and code artifacts; never invoke a function."""

from __future__ import annotations

import io
import zipfile
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from moto import mock_aws

from aws.managed.faas_lambda import LambdaConfig, LambdaDriver

LAMBDA_EFFECTS = (
    "create_function",
    "update_function_code",
    "update_function_configuration",
    "delete_function",
    "create_function_url_config",
    "update_function_url_config",
    "delete_function_url_config",
    "add_permission",
    "remove_permission",
    "tag_resource",
)
IAM_EFFECTS = (
    "create_role",
    "update_assume_role_policy",
    "put_role_policy",
    "delete_role_policy",
    "delete_role",
    "tag_role",
)


def no_effects(cloud):
    stack = ExitStack()
    spies = [
        stack.enter_context(patch.object(client, key, wraps=getattr(client, key)))
        for client, keys in ((cloud.api, LAMBDA_EFFECTS), (cloud.iam, IAM_EFFECTS))
        for key in keys
    ]
    return stack, spies


@contextmanager
def native_lambda():
    with mock_aws():
        api = boto3.client("lambda", region_name="us-east-1")
        iam = boto3.client("iam", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="astrolift-lambda-fixture-2032")
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("app.py", "def handler(event, context):\n    return {'ok': True}\n")
        s3.put_object(Bucket="astrolift-lambda-fixture-2032", Key="code.zip", Body=data.getvalue())
        cfg = LambdaConfig(region="us-east-1", account_id="123456789012")
        code = {
            "package_type": "zip",
            "s3_bucket": "astrolift-lambda-fixture-2032",
            "s3_key": "code.zip",
            "runtime": "python3.12",
            "handler": "app.handler",
            "public": True,
        }
        driver = LambdaDriver(config=cfg, client=api, iam_client=iam)
        yield SimpleNamespace(api=api, iam=iam, s3=s3, cfg=cfg, code=code, driver=driver)
