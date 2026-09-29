from __future__ import annotations

import json
from uuid import uuid4

import pytest

from aws._errors import ProviderError
from aws.registry_ecr import ECRConfig, ECRDriver


@pytest.fixture
def registry(ecr_client):
    driver = ECRDriver(config=ECRConfig(region="us-east-1", account_id="123456789012"), client=ecr_client)
    repo = driver.ensure_repo("acme/api")
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {
                "mediaType": "application/vnd.docker.container.image.v1+json",
                "size": 1,
                "digest": "sha256:" + "a" * 64,
            },
            "layers": [],
        }
    )
    response = ecr_client.put_image(repositoryName=repo.name, imageManifest=manifest, imageTag="sha-abc")
    return driver, repo, response["image"]["imageId"]["imageDigest"]


def test_pin_is_idempotent_and_preserves_digest(registry, ecr_client):
    driver, repo, digest = registry
    env, deployment = str(uuid4()), str(uuid4())
    pins = driver.retain_deployment_images([repo.uri + ":sha-abc"], environment=env, deployment=deployment)
    assert driver.retain_deployment_images([repo.uri + ":sha-abc"], environment=env, deployment=deployment) == pins
    assert pins[0]["pinned_ref"] == f"{repo.uri}@{digest}"
    current = ecr_client.describe_images(repositoryName=repo.name)["imageDetails"][0]
    assert set(current["imageTags"]) == {"sha-abc", pins[0]["tag"]}


def test_multiple_environments_do_not_share_pins(registry, ecr_client):
    driver, repo, digest = registry
    env_a, env_b, deployment = str(uuid4()), str(uuid4()), str(uuid4())
    a = driver.retain_deployment_images([f"{repo.uri}@{digest}"], environment=env_a, deployment=deployment)
    b = driver.retain_deployment_images([f"{repo.uri}@{digest}"], environment=env_b, deployment=deployment)
    driver.release_deployment_images(a, environment=env_a, deployment=deployment)
    tags = ecr_client.describe_images(repositoryName=repo.name)["imageDetails"][0]["imageTags"]
    assert a[0]["tag"] not in tags
    assert b[0]["tag"] in tags
    assert "sha-abc" in tags


def test_cannot_release_other_environment_pin(registry):
    driver, repo, _ = registry
    env, deployment = str(uuid4()), str(uuid4())
    pins = driver.retain_deployment_images([repo.uri + ":sha-abc"], environment=env, deployment=deployment)
    with pytest.raises(ProviderError, match="another deployment"):
        driver.release_deployment_images(pins, environment=str(uuid4()), deployment=deployment)


def test_release_never_deletes_last_tag(registry, ecr_client):
    driver, repo, _ = registry
    env, deployment = str(uuid4()), str(uuid4())
    pins = driver.retain_deployment_images([repo.uri + ":sha-abc"], environment=env, deployment=deployment)
    ecr_client.batch_delete_image(repositoryName=repo.name, imageIds=[{"imageTag": "sha-abc"}])
    driver.release_deployment_images(pins, environment=env, deployment=deployment)
    assert ecr_client.describe_images(repositoryName=repo.name)["imageDetails"][0]["imageTags"] == [pins[0]["tag"]]


def test_release_is_idempotent(registry):
    driver, repo, _ = registry
    env, deployment = str(uuid4()), str(uuid4())
    pins = driver.retain_deployment_images([repo.uri + ":sha-abc"], environment=env, deployment=deployment)
    driver.release_deployment_images(pins, environment=env, deployment=deployment)
    driver.release_deployment_images(pins, environment=env, deployment=deployment)


def test_external_registry_is_not_modified(registry):
    driver, _, _ = registry
    assert (
        driver.retain_deployment_images(["ghcr.io/acme/api:v1"], environment=str(uuid4()), deployment=str(uuid4()))
        == []
    )


def test_missing_image_cannot_be_silently_deployed(registry):
    driver, repo, _ = registry
    with pytest.raises(ProviderError, match="unavailable"):
        driver.retain_deployment_images([repo.uri + ":missing"], environment=str(uuid4()), deployment=str(uuid4()))


def test_repository_is_enrolled_on_create_and_adoption(registry, ecr_client):
    driver, repo, _ = registry
    arn = ecr_client.describe_repositories(repositoryNames=[repo.name])["repositories"][0]["repositoryArn"]
    assert {"Key": "astrolift.io/ecr-retention", "Value": "enabled"} in ecr_client.list_tags_for_resource(
        resourceArn=arn
    )["tags"]
    ecr_client.untag_resource(resourceArn=arn, tagKeys=["astrolift.io/ecr-retention"])
    driver.ensure_repo(repo.name)
    assert {"Key": "astrolift.io/ecr-retention", "Value": "enabled"} in ecr_client.list_tags_for_resource(
        resourceArn=arn
    )["tags"]


def test_tag_and_digest_ref_retains_canonical_digest(registry):
    driver, repo, digest = registry
    pins = driver.retain_deployment_images(
        [f"{repo.uri}:sha-abc@{digest}"], environment=str(uuid4()), deployment=str(uuid4())
    )
    assert pins[0]["pinned_ref"] == f"{repo.uri}@{digest}"


def test_mutable_repository_pins_distinguish_full_digest(ecr_client):
    from botocore.stub import Stubber

    driver = ECRDriver(
        config=ECRConfig(region="us-east-1", account_id="123456789012", image_tag_mutability="MUTABLE"),
        client=ecr_client,
    )
    environment, deployment = str(uuid4()), str(uuid4())
    digests = ["sha256:" + "a" * 12 + suffix * 52 for suffix in ("b", "c")]
    prefix = f"retain-astrolift-{environment.replace('-', '')}-{deployment.replace('-', '')}-"
    refs = ["123456789012.dkr.ecr.us-east-1.amazonaws.com/acme/api:" + tag for tag in ("v1", "v2")]
    with Stubber(ecr_client) as stubber:
        for ref, digest in zip(refs, digests, strict=True):
            image = {"imageId": {"imageDigest": digest}, "imageManifest": "{}"}
            stubber.add_response(
                "batch_get_image",
                {"images": [image]},
                {"repositoryName": "acme/api", "imageIds": [{"imageTag": ref.rsplit(":", 1)[1]}]},
            )
            stubber.add_response(
                "put_image",
                {"image": image},
                {"repositoryName": "acme/api", "imageManifest": "{}", "imageTag": prefix + digest[7:]},
            )
        pins = driver.retain_deployment_images(refs, environment=environment, deployment=deployment)
        stubber.assert_no_pending_responses()
    assert len({pin["tag"] for pin in pins}) == 2
    assert {pin["digest"] for pin in pins} == set(digests)
