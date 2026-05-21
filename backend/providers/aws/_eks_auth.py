"""Mint short-lived EKS bearer tokens via the AWS IAM Authenticator protocol.

EKS expects a bearer token of the form ``k8s-aws-v1.<b64url-presigned-url>``
where the presigned URL is an STS GetCallerIdentity request signed with
SigV4 and carries an ``x-k8s-aws-id: <cluster-name>`` header. EKS's
auth webhook decodes the URL, verifies the signature against the IAM
principal, and maps that principal to a Kubernetes user via the cluster's
``aws-auth`` ConfigMap (or EKS Access Entries on newer clusters).

We mint tokens natively via boto3's low-level ``SigV4QueryAuth`` rather
than shelling out to ``aws eks get-token`` — the worker container only
ships boto3, not the aws CLI, and forking per request adds latency anyway.

Why ``SigV4QueryAuth`` instead of ``RequestSigner.generate_presigned_url``:
the latter wraps the same signer in extra event-dispatch + ``request.prepare()``
layers that subtly alter the signed URL's query-string encoding.  The
``SigV4QueryAuth.add_auth(request)`` direct-call pattern produces the
byte-exact URL shape that the AWS IAM Authenticator accepts; mirrors
what working AWS-deployed Django apps ship in production.

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


_PRESIGN_EXPIRES_SECONDS = 900
"""Default presigned-URL lifetime when the caller doesn't pin one.

EKS's IAM authenticator caps at 15 minutes (900s); the k8s client
doesn't auto-retry on 401, so we ask for the EKS ceiling rather than
churning short-lived tokens mid-operation. Callers (notably the EKS
driver) override via ``EKSConfig.sts_token_lifetime_seconds`` — see
#359."""


def mint_eks_token(
    cluster_name: str,
    region: str,
    *,
    session: boto3.Session | None = None,
    expires_in_seconds: int = _PRESIGN_EXPIRES_SECONDS,
) -> str:
    """Return a ``k8s-aws-v1.<...>`` bearer token for ``cluster_name``.

    Uses the calling process's default boto3 session unless ``session``
    is provided — supports both static IAM credentials and ECS task
    role credentials transparently because botocore picks them up from
    the standard credential chain.

    ``expires_in_seconds`` controls the signed STS presigned-URL
    window; EKS's IAM authenticator caps it at 900 (15 minutes).

    Raises ``botocore.exceptions.NoCredentialsError`` when no creds are
    available; the activity surface translates that to a clear
    operator-facing error.
    """
    import boto3
    from botocore.auth import SigV4QueryAuth
    from botocore.awsrequest import AWSRequest

    sess = session or boto3.Session()
    # ``get_frozen_credentials()`` snapshots the rotating ECS task creds
    # at sign time so the URL signature isn't racing a refresh.  matches
    # the pattern proven against EKS in production AWS Django apps.
    credentials = sess.get_credentials().get_frozen_credentials()

    request = AWSRequest(
        method="GET",
        url=f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        # The cluster-name header is what binds this signed URL to a
        # specific EKS cluster — without it the IAM principal would be
        # valid against any cluster the caller could reach.
        headers={"x-k8s-aws-id": cluster_name},
    )
    SigV4QueryAuth(credentials, "sts", region, expires=expires_in_seconds).add_auth(request)

    # base64url-encode the URL and strip trailing '=' padding (the
    # authenticator rejects padded tokens — protocol quirk).
    encoded = base64.urlsafe_b64encode(request.url.encode("utf-8")).rstrip(b"=").decode("utf-8")
    return "k8s-aws-v1." + encoded


__all__ = ["mint_eks_token"]
