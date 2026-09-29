"""Bedrock bindings grant the exact resources an inference profile needs (#2137).

``us.anthropic.claude-sonnet-4-6`` is a cross-region inference profile. The
driver granted ``foundation-model/us.anthropic.claude-sonnet-4-6``, which is
not a resource, so the workload's role could never invoke it.
"""

from __future__ import annotations

from typing import Any

import pytest

from _sdk.managed_service import ServiceHandle
from aws.managed._base import ManagedServiceError
from aws.managed.model_endpoint_bedrock import (
    KIND,
    AmazonBedrockConfig,
    AmazonBedrockDriver,
    is_inference_profile,
)

PROFILE = "us.anthropic.claude-sonnet-4-6"
PROFILE_ARN = "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.anthropic.claude-sonnet-4-6"
DESTINATIONS = [
    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-6",
    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6",
    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-6",
]


class ProfileBedrock:
    def __init__(self, fail: Exception | None = None, models: list[str] | None = None):
        self.fail = fail
        self.models = DESTINATIONS if models is None else models
        self.asked: list[str] = []

    def get_inference_profile(self, *, inferenceProfileIdentifier: str) -> dict[str, Any]:  # noqa: N803 - boto3 name
        self.asked.append(inferenceProfileIdentifier)
        if self.fail:
            raise self.fail
        return {"inferenceProfileArn": PROFILE_ARN, "models": [{"modelArn": m} for m in self.models]}


class NoLogs:
    pass


def _fresh(bedrock) -> AmazonBedrockDriver:
    # A fresh instance, as the finalize activity builds it: no in-memory record.
    return AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-west-2"), bedrock_client=bedrock, logs_client=NoLogs()
    )


def _grants(driver, model_id):
    binding = driver.binding(ServiceHandle(handle=f"{KIND}/veruus-portal-prod-model"), config={"model_id": model_id})
    return binding, {g.resource: sorted(g.actions) for g in binding.iam_grants}


@pytest.mark.parametrize(
    "model_id",
    [PROFILE, "eu.anthropic.claude-3-haiku-20240307-v1:0", "global.anthropic.claude-sonnet-4-6", PROFILE_ARN],
)
def test_profiles_are_recognised(model_id):
    assert is_inference_profile(model_id)


@pytest.mark.parametrize(
    "model_id",
    ["anthropic.claude-3-haiku-20240307-v1:0", "amazon.titan-text-express-v1", "mistral.mistral-large-2402-v1:0"],
)
def test_foundation_models_are_not_profiles(model_id):
    assert not is_inference_profile(model_id)


def test_a_profile_grants_itself_and_every_destination_model():
    bedrock = ProfileBedrock()

    binding, grants = _grants(_fresh(bedrock), PROFILE)

    assert set(grants) == {PROFILE_ARN, *DESTINATIONS}
    assert all(
        actions == ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"] for actions in grants.values()
    )
    assert not any("foundation-model/us." in resource for resource in grants)
    assert bedrock.asked == [PROFILE]
    # The workload still calls the profile id.
    assert binding.env_vars["BEDROCK_MODEL_ID"].literal == PROFILE


def test_a_foundation_model_keeps_its_one_grant_and_no_lookup():
    bedrock = ProfileBedrock()

    _binding, grants = _grants(_fresh(bedrock), "anthropic.claude-3-haiku-20240307-v1:0")

    assert list(grants) == ["arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"]
    assert bedrock.asked == []


def test_a_profile_that_cannot_be_read_fails_closed():
    driver = _fresh(ProfileBedrock(fail=RuntimeError("AccessDeniedException: not authorized")))

    with pytest.raises(ManagedServiceError, match="could not be resolved"):
        _grants(driver, PROFILE)


def test_a_profile_with_no_destinations_fails_closed():
    with pytest.raises(ManagedServiceError, match="no destination models"):
        _grants(_fresh(ProfileBedrock(models=[])), PROFILE)
