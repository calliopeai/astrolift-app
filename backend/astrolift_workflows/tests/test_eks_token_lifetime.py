"""
EKS bearer-token lifetime default (#359).

The kubernetes-client used by ``EKSClusterDriver`` does not auto-retry
on 401, so a too-short presigned-URL window can cause spurious
mid-operation auth failures. EKS's IAM authenticator caps the window
at 15 minutes (900s) and that's what we ask for by default. This test
guards against the default silently regressing back to a short window
(which was the original value before #359).

The intended test path called out in #359 was
``tests/aws/test_eks_exec_plugin_token.py``; that directory does not
exist in this tree (vendor providers ship their own tests in a
separate repo), so the assertion lives in ``astrolift_workflows``
where the driver is consumed.
"""

from __future__ import annotations


def test_eks_config_default_sts_token_lifetime_is_900_seconds():
    from aws.cluster_eks import EKSConfig

    cfg = EKSConfig(region="us-west-2", cluster_name="dummy")
    assert cfg.sts_token_lifetime_seconds == 900


def test_eks_auth_module_default_presign_expiry_is_900_seconds():
    """``mint_eks_token`` falls back to ``_PRESIGN_EXPIRES_SECONDS``
    when the caller doesn't pin a window. Defaulting it to the EKS
    ceiling means callers that pre-date the keyword (legacy CLI
    paths) get the longer window automatically."""
    from aws import _eks_auth

    assert _eks_auth._PRESIGN_EXPIRES_SECONDS == 900
