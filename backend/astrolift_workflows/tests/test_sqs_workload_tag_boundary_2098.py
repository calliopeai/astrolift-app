"""Production binding/compiler/IRSA paths remove stale workload admin authority."""

from __future__ import annotations

from fnmatch import fnmatchcase

import pytest
from aws.identity_irsa import IRSAConfig, IRSADriver

from astrolift_workflows.activities.managed_service_lifecycle import _managed_binding_for
from astrolift_workflows.activities.workload_identity import _permissions_from_bindings
from astrolift_workflows.tests.test_aws_live_ownership_2098 import (
    world,  # noqa: F401 - actual persisted/SDK fixture
)
from providers.tests.aws.test_identity_irsa import _RecordingIam

pytestmark = pytest.mark.django_db


def _allowed_by_issued_policy(policy, action, resource):
    """Evaluate the emitted unconditional Allow/action/resource subset only.

    This is a captured policy regression, not a live AWS IAM simulator. No
    conditions or unrelated administrator/resource policies are invented.
    """
    matching = []
    for statement in policy["Statement"]:
        assert not ({"Condition", "NotAction", "NotResource"} & statement.keys())
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        resources = (
            statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]]
        )
        if any(fnmatchcase(action.lower(), pattern.lower()) for pattern in actions) and any(
            fnmatchcase(resource, pattern) for pattern in resources
        ):
            matching.append(statement["Effect"])
    return "Allow" in matching and "Deny" not in matching


@pytest.mark.parametrize("world", ["sqs"], indirect=True)
@pytest.mark.parametrize("mode", ["send", "consume", "both", "manage"])
def test_actual_owner_binding_compiles_and_reconciles_irsa_without_tag_or_policy_editing(
    world,  # noqa: F811 - imported pytest fixture
    mode,
):
    world.svc.config = {"access_mode": mode}
    world.svc.save(update_fields=["config"])
    binding = _managed_binding_for(world.svc)
    arn = binding.env_vars["QUEUE_ARN_OR_ID"].literal
    permissions = _permissions_from_bindings([binding], plugin_slug="aws")
    iam = _RecordingIam()
    identity = IRSADriver(
        config=IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="oidc.eks.us-east-1.amazonaws.com/id/ABC123",
        ),
        iam_client=iam,
    )
    role_name = "scoped-sqs-app-2098"
    # Existing apps can retain their old admin grant until identity reconcile.
    identity.create_identity_role(
        role_name,
        permissions=[
            {
                "Effect": "Allow",
                "Action": ["sqs:TagQueue", "sqs:SetQueueAttributes"],
                "Resource": arn,
            }
        ],
    )
    assert _allowed_by_issued_policy(
        iam.inline_policies[(role_name, "astrolift-workload-policy")], "sqs:TagQueue", arn
    )
    identity.create_identity_role(role_name, permissions=permissions)
    policy = iam.inline_policies[(role_name, "astrolift-workload-policy")]
    assert _allowed_by_issued_policy(policy, "sqs:GetQueueAttributes", arn)
    assert _allowed_by_issued_policy(policy, "sqs:SendMessage", arn) == (mode in {"send", "both", "manage"})
    assert _allowed_by_issued_policy(policy, "sqs:ReceiveMessage", arn) == (
        mode in {"consume", "both", "manage"}
    )
    assert _allowed_by_issued_policy(policy, "sqs:PurgeQueue", arn) == (mode == "manage")
    for action in (
        "sqs:TagQueue",
        "sqs:UntagQueue",
        "sqs:SetQueueAttributes",
        "sqs:AddPermission",
        "sqs:RemovePermission",
        "sqs:DeleteQueue",
        "sqs:CreateQueue",
    ):
        assert not _allowed_by_issued_policy(policy, action, arn), action
    for action in ("sqs:GetQueueAttributes", "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:PurgeQueue"):
        assert not _allowed_by_issued_policy(policy, action, arn + "-another-app"), action
    if mode == "manage":
        assert "queue purging" in binding.notes and "platform-only" in binding.notes
