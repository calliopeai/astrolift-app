"""Tests for ECR ImageRegistryDriver (#34)."""

from __future__ import annotations

import base64
import json

import pytest

from aws._errors import NotFoundError, ProviderError
from aws.registry_ecr import ECRConfig, ECRDriver


@pytest.fixture
def driver(ecr_client) -> ECRDriver:
    return ECRDriver(
        config=ECRConfig(
            region="us-east-1",
            account_id="123456789012",
        ),
        client=ecr_client,
    )


# ---- ensure_repo -------------------------------------------------


def test_ensure_repo_creates_new(driver: ECRDriver) -> None:
    repo = driver.ensure_repo("acme/api")
    assert repo.name == "acme/api"
    assert repo.uri.endswith("/acme/api")


def test_ensure_repo_idempotent(driver: ECRDriver) -> None:
    """Second call returns same repo URI without erroring."""
    a = driver.ensure_repo("acme/api")
    b = driver.ensure_repo("acme/api")
    assert a.uri == b.uri


def test_ensure_repo_immutable_tags_default(driver: ECRDriver, ecr_client) -> None:
    """IMMUTABLE is the production hygiene default."""
    driver.ensure_repo("acme/api")
    desc = ecr_client.describe_repositories(repositoryNames=["acme/api"])
    assert desc["repositories"][0]["imageTagMutability"] == "IMMUTABLE"


def test_ensure_repo_scan_on_push_default(driver: ECRDriver, ecr_client) -> None:
    driver.ensure_repo("acme/api")
    desc = ecr_client.describe_repositories(repositoryNames=["acme/api"])
    cfg = desc["repositories"][0].get("imageScanningConfiguration", {})
    assert cfg.get("scanOnPush") is True


def test_ensure_repo_with_kms_encryption(ecr_client) -> None:
    """KMS-managed encryption when operator supplies a key."""
    driver = ECRDriver(
        config=ECRConfig(
            region="us-east-1",
            account_id="123456789012",
            encryption_kms_key_id="arn:aws:kms:us-east-1:123:key/abc",
        ),
        client=ecr_client,
    )
    repo = driver.ensure_repo("acme/secure")
    assert repo.name == "acme/secure"


def test_ensure_repo_mutable_when_configured(ecr_client) -> None:
    driver = ECRDriver(
        config=ECRConfig(
            region="us-east-1",
            account_id="123456789012",
            image_tag_mutability="MUTABLE",
        ),
        client=ecr_client,
    )
    driver.ensure_repo("acme/legacy")
    desc = ecr_client.describe_repositories(
        repositoryNames=["acme/legacy"],
    )
    assert desc["repositories"][0]["imageTagMutability"] == "MUTABLE"


# ---- delete_repo -------------------------------------------------


def test_delete_repo_force_removes(driver: ECRDriver, ecr_client) -> None:
    """archive=False uses force=True so repos with images delete."""
    driver.ensure_repo("acme/old")
    driver.delete_repo("acme/old", archive=False)
    response = ecr_client.describe_repositories()
    assert all(r["repositoryName"] != "acme/old" for r in response["repositories"])


def test_delete_repo_archive_blocks_push(
    driver: ECRDriver,
    ecr_client,
) -> None:
    """archive=True keeps the repo but applies a deny-push policy
    so deployed pods can still pull until torn down."""
    driver.ensure_repo("acme/archived")
    driver.delete_repo("acme/archived", archive=True)
    # Repo still exists
    response = ecr_client.describe_repositories(
        repositoryNames=["acme/archived"],
    )
    assert response["repositories"][0]["repositoryName"] == "acme/archived"
    # Policy denies push
    policy_resp = ecr_client.get_repository_policy(
        repositoryName="acme/archived",
    )
    policy = json.loads(policy_resp["policyText"])
    assert any(s["Effect"] == "Deny" and "ecr:PutImage" in s.get("Action", []) for s in policy["Statement"])


def test_delete_repo_not_found_is_idempotent(driver: ECRDriver) -> None:
    # #998: deleting an absent repo is the desired end state — succeed
    # so teardown re-runs complete instead of halting.
    driver.delete_repo("acme/never-existed", archive=False)


# ---- get_pull_secret ----------------------------------------------


def test_get_pull_secret_shape(driver: ECRDriver) -> None:
    secret = driver.get_pull_secret(cluster="aws-prod", namespace="acme-api")
    assert secret["apiVersion"] == "v1"
    assert secret["kind"] == "Secret"
    assert secret["type"] == "kubernetes.io/dockerconfigjson"
    assert secret["metadata"]["name"] == "astrolift-ecr-credentials"
    assert secret["metadata"]["namespace"] == "acme-api"


def test_get_pull_secret_dockerconfig_decodes(driver: ECRDriver) -> None:
    """The dockerconfigjson must be valid base64-encoded JSON with
    an 'auths' map."""
    secret = driver.get_pull_secret(cluster="aws-prod", namespace="ns")
    encoded = secret["data"][".dockerconfigjson"]
    decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert "auths" in decoded
    # exactly one registry entry
    assert len(decoded["auths"]) == 1
    auth_value = next(iter(decoded["auths"].values()))
    # Re-decode the auth field — should be "AWS:<password>"
    auth_decoded = base64.b64decode(auth_value["auth"]).decode("utf-8")
    assert auth_decoded.startswith("AWS:")


def test_pull_secret_tracks_expiry_annotation(driver: ECRDriver) -> None:
    """ECR token expires every 12h — annotation prompts the
    refresh-scheduler workflow."""
    secret = driver.get_pull_secret(cluster="x", namespace="y")
    annotation = secret["metadata"]["annotations"]["astrolift.io/expires-at-utc-hours"]
    assert annotation == "12"


# ---- push --------------------------------------------------------


def test_push_returns_canonical_ref(driver: ECRDriver) -> None:
    """boto3 doesn't push image layers; this returns the ref the
    build runner pushes to."""
    ref = driver.push(
        local_image="my-app:dev",
        repo="acme/api",
        tag="v1.2.3",
    )
    assert ref == "123456789012.dkr.ecr.us-east-1.amazonaws.com/acme/api:v1.2.3"


def test_push_creates_repo_if_missing(driver: ECRDriver, ecr_client) -> None:
    """push() ensures the repo exists first — first-deploy on a
    fresh app must work without explicit ensure_repo from caller."""
    driver.push(
        local_image="my-app:dev",
        repo="brand-new/repo",
        tag="v1",
    )
    response = ecr_client.describe_repositories(
        repositoryNames=["brand-new/repo"],
    )
    assert response["repositories"][0]["repositoryName"] == "brand-new/repo"


def test_push_rejects_empty_local_image(driver: ECRDriver) -> None:
    with pytest.raises(ValueError):
        driver.push(local_image="", repo="r", tag="t")


# ---- list_tags ----------------------------------------------------


def test_list_tags_returns_empty_for_fresh_repo(driver: ECRDriver) -> None:
    driver.ensure_repo("acme/empty")
    assert driver.list_tags("acme/empty") == []


def test_list_tags_not_found(driver: ECRDriver) -> None:
    with pytest.raises(NotFoundError):
        driver.list_tags("acme/never-existed")


# ---- error mapping -----------------------------------------------


def test_unknown_repo_error_typed(driver: ECRDriver) -> None:
    """Errors must propagate as typed ProviderError, not raw ClientError."""
    with pytest.raises(ProviderError):
        driver.list_tags("nonexistent/repo")


# ---- ensure_ci_push_role: IAM charset (#1026) --------------------


class _RecordingIam:
    """Minimal IAM stand-in that records the create_role call.

    moto doesn't enforce IAM's role-Description charset, so a real
    regression guard has to inspect what the driver *sends* to CreateRole.
    """

    class exceptions:  # noqa: N801 — mirrors boto3's lowercase ``client.exceptions``
        class EntityAlreadyExistsException(Exception):
            pass

        class NoSuchEntityException(Exception):
            pass

    def __init__(self) -> None:
        self.created: dict = {}

    def create_role(self, **kwargs):
        self.created = kwargs
        return {"Role": {"Arn": f"arn:aws:iam::123456789012:role/{kwargs['RoleName']}"}}

    def put_role_policy(self, **kwargs):
        return {}

    def update_assume_role_policy(self, **kwargs):
        return {}

    def get_role(self, **kwargs):
        return {"Role": {"Arn": f"arn:aws:iam::123456789012:role/{kwargs['RoleName']}"}}


def test_ensure_ci_push_role_description_is_ascii() -> None:
    """IAM rejects a role Description outside [ASCII + Latin-1], so the
    description must never carry a unicode arrow/dash — a "→" failed
    CreateRole on every onboard (#1026).

    Uses a recording IAM stand-in (not moto): ``ensure_ci_push_role`` only
    touches the IAM client, so no ECR client is exercised."""
    iam = _RecordingIam()
    driver = ECRDriver(
        config=ECRConfig(region="us-east-1", account_id="123456789012"),
        client=object(),
        iam_client=iam,
    )
    driver.ensure_ci_push_role(
        repo="steadymd/web",
        scm_provider="github",
        scm_repo_full_name="calliopeai/astrolift-sample-web",
    )
    desc = iam.created.get("Description", "")
    assert desc, "create_role was not called with a Description"
    assert desc.isascii(), f"IAM role Description must be ASCII (#1026): {desc!r}"


# ---- ensure_ci_push_role: ID-stamped OIDC subjects (#1532) -------


def _trust_sub_patterns(iam: _RecordingIam) -> list[str]:
    import json

    doc = json.loads(iam.created["AssumeRolePolicyDocument"])
    return doc["Statement"][0]["Condition"]["StringLike"]["token.actions.githubusercontent.com:sub"]


def test_ensure_ci_push_role_trusts_id_stamped_subject() -> None:
    """Newly created GitHub repos present ID-stamped OIDC subjects
    (``repo:org@OWNER_ID/name@REPO_ID:...``); the trust policy must list
    that pattern alongside the login-based one or the assume fails with
    a bare "Not authorized" (#1532)."""
    iam = _RecordingIam()
    driver = ECRDriver(
        config=ECRConfig(region="us-east-1", account_id="123456789012"),
        client=object(),
        iam_client=iam,
    )
    driver.ensure_ci_push_role(
        repo="steadymd/web",
        scm_provider="github",
        scm_repo_full_name="steadymd/hello-astro-demo",
        scm_repo_numeric_ids=(19630436, 1342155079),
    )
    assert _trust_sub_patterns(iam) == [
        "repo:steadymd/hello-astro-demo:*",
        "repo:steadymd@19630436/hello-astro-demo@1342155079:*",
    ]


def test_ensure_ci_push_role_without_ids_trusts_login_subject_only() -> None:
    """No numeric ids (lookup failed / unavailable) degrades to the
    login-based pattern — never a wildcard that another repo could
    satisfy."""
    iam = _RecordingIam()
    driver = ECRDriver(
        config=ECRConfig(region="us-east-1", account_id="123456789012"),
        client=object(),
        iam_client=iam,
    )
    driver.ensure_ci_push_role(
        repo="steadymd/web",
        scm_provider="github",
        scm_repo_full_name="steadymd/hello-astro-demo",
    )
    assert _trust_sub_patterns(iam) == ["repo:steadymd/hello-astro-demo:*"]
