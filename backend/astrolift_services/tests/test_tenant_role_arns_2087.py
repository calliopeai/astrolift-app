"""Roles AWS assumes that the #1960 ``role_arn`` key rule missed (#2087).

A Redshift cluster or namespace assumes every role in ``iam_roles``; API
Gateway assumes the role in an integration's or authorizer's ``credentials``;
a Firehose Lambda processor is invoked with the role in its ``RoleArn``
parameter. None of those keys ends in ``role_arn``, so a tenant could name the
platform's or another org's role there. They must sit under the owning org's
IAM role path like every other role a config names.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_services.secret_ref_config import org_role_path, unscoped_config_secret_refs

FOREIGN = "arn:aws:iam::123456789012:role/astrolift-platform-admin"


def _owner():
    return SimpleNamespace(organization=SimpleNamespace(guid=uuid.uuid4()), guid=uuid.uuid4())


def _own_role(owner, name: str = "warehouse-copy") -> str:
    return f"arn:aws:iam::123456789012:role{org_role_path(owner.organization)}{name}"


def _reasons(config, owner):
    return [reason for _path, _ref, reason in unscoped_config_secret_refs(config, owner=owner, cluster=None)]


def _integration(credentials: str) -> dict:
    return {
        "paths": {
            "/items": {
                "get": {
                    "x-amazon-apigateway-integration": {
                        "type": "aws",
                        "uri": "arn:aws:apigateway:us-east-1:s3:path/bucket/{key}",
                        "credentials": credentials,
                    },
                },
            },
        },
    }


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"iam_roles": ["{own}", "{foreign}"]}, id="redshift-iam-roles"),
        pytest.param({"IamRoles": ["{foreign}"]}, id="native-spelling"),
        pytest.param({"openapi": _integration("{foreign}")}, id="rest-integration-credentials"),
        pytest.param(
            {
                "openapi": {
                    "components": {
                        "securitySchemes": {
                            "lambda": {
                                "x-amazon-apigateway-authorizer": {"authorizerCredentials": "{foreign}"}
                            },
                        },
                    },
                },
            },
            id="rest-authorizer-credentials",
        ),
        pytest.param(
            {"integrations": [{"request": {"CredentialsArn": "{foreign}"}}]}, id="websocket-credentials"
        ),
        pytest.param(
            {
                "destination": {
                    "configuration": {
                        "ProcessingConfiguration": {
                            "Processors": [
                                {
                                    "Type": "Lambda",
                                    "Parameters": [
                                        {"ParameterName": "LambdaArn", "ParameterValue": "arn:aws:lambda:x"},
                                        {"ParameterName": "RoleArn", "ParameterValue": "{foreign}"},
                                    ],
                                },
                            ],
                        },
                    },
                },
            },
            id="firehose-processor-role",
        ),
    ],
)
def test_a_role_aws_assumes_outside_the_orgs_path_is_refused(config):
    owner = _owner()
    filled = eval(  # noqa: S307 - test fixture templating
        repr(config).replace("{own}", _own_role(owner)).replace("{foreign}", FOREIGN)
    )

    reasons = _reasons(filled, owner)

    assert any(FOREIGN in reason and "IAM role path" in reason for reason in reasons)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"iam_roles": ["{own}"]}, id="redshift-iam-roles"),
        pytest.param({"openapi": _integration("{own}")}, id="rest-integration-credentials"),
        # The caller's own credentials pass through; no platform identity.
        pytest.param({"openapi": _integration("arn:aws:iam::*:user/*")}, id="caller-credentials"),
        pytest.param({"credentials": {"username": "x"}}, id="unrelated-credentials-object"),
    ],
)
def test_the_orgs_own_roles_and_non_role_credentials_pass(config):
    owner = _owner()
    filled = eval(repr(config).replace("{own}", _own_role(owner)))  # noqa: S307 - test fixture templating

    assert _reasons(filled, owner) == []


@pytest.mark.parametrize(
    "document",
    [
        "paths:\n  /items:\n    get:\n      x-amazon-apigateway-integration:\n        credentials: {arn}\n",
        '{"paths": {"/items": {"get": {"x-amazon-apigateway-integration": {"credentials": "{arn}"}}}}}',
        b"authorizerCredentials: {arn}\n",
    ],
)
def test_credentials_in_a_text_document_are_refused_even_the_orgs_own(document):
    # Text reaches API Gateway as written, where a parser the check does not
    # share decides which credentials apply.
    owner = _owner()
    own = _own_role(owner)
    filled = (
        document.replace(b"{arn}", own.encode())
        if isinstance(document, bytes)
        else document.replace("{arn}", own)
    )

    reasons = _reasons({"openapi": filled}, owner)

    assert any("give the document as an object" in reason for reason in reasons)


def test_a_text_document_without_credentials_passes():
    owner = _owner()
    document = "openapi: 3.0.1\ninfo:\n  title: Items\n  description: Send your credentials in a header.\n"

    assert _reasons({"openapi": document}, owner) == []
