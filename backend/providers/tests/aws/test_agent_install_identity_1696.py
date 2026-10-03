"""A private EKS install signs with the same registered STS identity as discovery."""

import base64
import datetime as dt
from urllib.parse import parse_qs, urlsplit

import pytest

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from aws import session
from aws.cluster_eks import EKSClusterDriver, EKSConfig


@pytest.fixture(autouse=True)
def empty_assumption_cache():
    session.clear_credential_cache()
    yield
    session.clear_credential_cache()


def test_registered_role_and_external_id_drive_actual_sigv4_token(monkeypatch, caplog):
    calls = []

    class Sts:
        def assume_role(self, **kwargs):
            calls.append(kwargs)
            return {
                "Credentials": {
                    "AccessKeyId": "ASIA-DISPOSABLE-REGISTERED",
                    "SecretAccessKey": "disposable-signing-secret",
                    "SessionToken": "disposable-session-token",
                    "Expiration": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
                }
            }

    monkeypatch.setattr(session, "_default_build", lambda service, **kw: Sts())
    credential = CloudCredential(
        cloud="aws",
        mode=CredentialMode.AWS_ASSUME_ROLE,
        role_arn="arn:aws:iam::000000000000:role/disposable",
        external_id="disposable-external-id",
        session_name="disposable-install",
    )
    eks_clients = []

    def build(service, **kwargs):
        eks_clients.append((service, kwargs))
        return object()

    session.aws_client("eks", region="us-west-2", credential=credential, build=build)
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="disposable-eks", credential=credential),
        eks_client=object(),
        sts_client=object(),
        ec2_client=object(),
    )
    token = driver._eks_token()
    query = parse_qs(urlsplit(base64.urlsafe_b64decode(token.split(".", 1)[1] + "==").decode()).query)
    assert query["X-Amz-Credential"][0].startswith("ASIA-DISPOSABLE-REGISTERED/")
    assert query["X-Amz-Security-Token"] == ["disposable-session-token"]
    assert query["X-Amz-SignedHeaders"] == ["host;x-k8s-aws-id"]
    assert eks_clients[0][1]["aws_access_key_id"] == "ASIA-DISPOSABLE-REGISTERED"
    assert calls == [
        {
            "RoleArn": credential.role_arn,
            "RoleSessionName": credential.session_name,
            "DurationSeconds": 3600,
            "ExternalId": credential.external_id,
        }
    ]
    assert token not in caplog.text


@pytest.mark.parametrize("credential", [None, CloudCredential(cloud="aws")])
def test_ambient_session_still_uses_default_chain(credential):
    calls = []
    result = session.aws_session(
        region="us-west-2", credential=credential, build=lambda **kw: calls.append(kw) or "ambient"
    )
    assert result == "ambient" and calls == [{"region_name": "us-west-2"}]
