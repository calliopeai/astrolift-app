"""Mint short-lived EKS bearer tokens via the AWS IAM Authenticator protocol.

EKS expects a bearer token of the form ``k8s-aws-v1.<b64url-presigned-url>``
where the presigned URL is an STS GetCallerIdentity request signed with
SigV4 and carries an ``x-k8s-aws-id: <cluster-name>`` header. EKS's
auth webhook decodes the URL, verifies the signature against the IAM
principal, and maps that principal to a Kubernetes user via the cluster's
``aws-auth`` ConfigMap (or EKS Access Entries on newer clusters).

We mint tokens natively via boto3's ``RequestSigner`` rather than shelling
out to ``aws eks get-token`` — the worker container only ships boto3, not
the aws CLI, and forking per request adds latency anyway.

Token TTL: the signed STS request is valid for 15 minutes of clock skew
either side of the signing timestamp. Callers should re-mint per
operation rather than caching, which is what ``EKSClusterDriver._eks_token``
already does.

Spec refs: https://github.com/kubernetes-sigs/aws-iam-authenticator
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import boto3


_PRESIGN_EXPIRES_SECONDS = 60
"""How long the presigned URL is valid before EKS rejects the auth.

EKS's authenticator allows up to 15 minutes; 60s is short enough to keep
old leaked tokens useless and long enough to absorb clock drift between
the worker host and the IAM/STS service. The driver re-mints per
operation so this isn't a refresh interval."""


def mint_eks_token(
    cluster_name: str,
    region: str,
    *,
    session: boto3.Session | None = None,
) -> str:
    """Return a ``k8s-aws-v1.<...>`` bearer token for ``cluster_name``.

    Uses the calling process's default boto3 session unless ``session``
    is provided — supports both static IAM credentials and ECS task
    role credentials transparently because botocore picks them up from
    the standard credential chain.

    Raises ``botocore.exceptions.NoCredentialsError`` when no creds are
    available; the activity surface translates that to a clear
    operator-facing error.
    """
    import boto3
    from botocore.signers import RequestSigner

    sess = session or boto3.Session()
    sts = sess.client("sts", region_name=region)
    signer = RequestSigner(
        sts.meta.service_model.service_id,
        region,
        "sts",
        "v4",
        sess.get_credentials(),
        sess.events,
    )
    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        # The cluster-name header is what binds this signed URL to a
        # specific EKS cluster — without it the IAM principal would be
        # valid against any cluster the caller could reach.
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }
    signed_url = signer.generate_presigned_url(
        params,
        region_name=region,
        expires_in=_PRESIGN_EXPIRES_SECONDS,
        operation_name="",
    )
    # base64url-encode the URL and strip trailing '=' padding (the
    # authenticator rejects padded tokens — protocol quirk).
    encoded = base64.urlsafe_b64encode(signed_url.encode("utf-8")).rstrip(b"=").decode("utf-8")
    return "k8s-aws-v1." + encoded


__all__ = ["mint_eks_token"]
