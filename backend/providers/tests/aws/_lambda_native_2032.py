"""Actual boto3/Moto Lambda, IAM and code artifacts; never invoke a function."""

from __future__ import annotations

import io
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace

import boto3
from moto import mock_aws

from aws.managed.faas_lambda import LambdaConfig, LambdaDriver


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
