"""Regression: teardown deletes must be idempotent (#998).

``delete_identity_role`` / ``delete_repo`` used to re-raise ``NotFoundError``
when the underlying AWS entity was already absent. A *delete* whose target is
gone has already met its goal — re-raising halted the app-teardown workflow at
``tearing_down`` (it never reached ``mark_deregistered``), orphaning the app +
its remaining cloud resources, and breaking re-runs. These assert the deletes
swallow the "not found" exception and return cleanly.
"""

from __future__ import annotations

from types import SimpleNamespace

from providers.aws.identity_irsa import IRSAConfig, IRSADriver
from providers.aws.registry_ecr import ECRConfig, ECRDriver


class _NoSuchEntity(Exception):
    pass


class _RepoNotFound(Exception):
    pass


class _RepoPolicyNotFound(Exception):
    pass


def _mock_iam():
    iam = SimpleNamespace()
    iam.exceptions = SimpleNamespace(NoSuchEntityException=_NoSuchEntity)

    def _raise(*_a, **_k):
        raise _NoSuchEntity("role does not exist")

    iam.list_role_policies = _raise
    iam.list_attached_role_policies = _raise
    iam.delete_role = _raise
    return iam


def _mock_ecr():
    ecr = SimpleNamespace()
    # Real botocore clients expose every exception class the service
    # defines as an attribute of `.exceptions`, whether or not a given call
    # raises it — an `except client.exceptions.X` that never matches still
    # has to evaluate `X` without an AttributeError. Nothing below raises
    # RepositoryPolicyNotFoundException (get_repository_policy raises the
    # not-found-repo one instead), but _non_archive_statements checks for
    # it first, so it has to exist here too.
    ecr.exceptions = SimpleNamespace(
        RepositoryNotFoundException=_RepoNotFound,
        RepositoryPolicyNotFoundException=_RepoPolicyNotFound,
    )

    def _raise(*_a, **_k):
        raise _RepoNotFound("repository does not exist")

    ecr.delete_repository = _raise
    # The archive path (_block_push) reads the existing policy first (to
    # preserve any operator statement, #1819 review), then writes the
    # merged one back; a missing repo raises RepositoryNotFoundException
    # from both calls.
    ecr.get_repository_policy = _raise
    ecr.set_repository_policy = _raise
    return ecr


def test_delete_identity_role_idempotent_when_role_missing():
    drv = IRSADriver(
        config=IRSAConfig(region="us-west-2", account_id="1", cluster_oidc_issuer="oidc.example"),
        iam_client=_mock_iam(),
    )
    # Must NOT raise — a missing role is the desired end state of a delete.
    drv.delete_identity_role("astrolift-acme-missing")


def test_delete_repo_idempotent_when_repo_missing():
    drv = ECRDriver(
        config=ECRConfig(region="us-west-2", account_id="1"),
        client=_mock_ecr(),
    )
    drv.delete_repo("astrolift-acme-missing", archive=False)


def test_archive_repo_idempotent_when_repo_missing():
    # The deregister workflow archives (archive=True) the repo; if it's
    # already gone, block-push must no-op rather than raise NotFoundError
    # (which gated soft-delete and wedged apps at tearing_down) — #1007.
    drv = ECRDriver(
        config=ECRConfig(region="us-west-2", account_id="1"),
        client=_mock_ecr(),
    )
    drv.delete_repo("astrolift-acme-missing", archive=True)
