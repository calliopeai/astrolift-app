"""Shared fixtures for AWS driver tests."""

from __future__ import annotations

import os
from collections.abc import Generator

import boto3
import pytest


@pytest.fixture(autouse=True)
def aws_credentials() -> None:
    """Ensure boto3 doesn't pick up real AWS creds — moto needs the
    fake env to engage. autouse so every test in this dir gets it."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def ecr_client() -> Generator:
    """boto3 ECR client wrapped with moto."""
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("ecr", region_name="us-east-1")


@pytest.fixture
def secrets_client() -> Generator:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("secretsmanager", region_name="us-east-1")


@pytest.fixture
def ssm_client() -> Generator:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def iam_client() -> Generator:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("iam", region_name="us-east-1")


@pytest.fixture
def route53_client() -> Generator:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("route53", region_name="us-east-1")


@pytest.fixture
def acm_client() -> Generator:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("acm", region_name="us-east-1")
