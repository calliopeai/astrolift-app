"""LifecycleMutation — assembled from per-feature mixin modules."""

from __future__ import annotations

import strawberry

from astrolift_lifecycle.schema.mutations.app_ops import AppOpsMutations
from astrolift_lifecycle.schema.mutations.deploy_tokens import DeployTokenMutations
from astrolift_lifecycle.schema.mutations.deployments import DeploymentMutations
from astrolift_lifecycle.schema.mutations.deprovision import DeprovisionMutations
from astrolift_lifecycle.schema.mutations.deregister import DeregisterMutations
from astrolift_lifecycle.schema.mutations.domains import DomainMutations
from astrolift_lifecycle.schema.mutations.environment_settings import EnvironmentSettingMutations
from astrolift_lifecycle.schema.mutations.environments import EnvironmentMutations
from astrolift_lifecycle.schema.mutations.helpers import (  # noqa: F401
    _BRANCH_SLUG_BAD_CHARS,
    _BULK_APPROVE_REJECT_CAP,
    _DEPLOY_PIPELINE_DISABLED_MSG,
    _VALID_TRIGGER_KINDS,
    _WEBHOOK_TRIGGER_KINDS,
    _abort_extras,
    _actor_from_request,
    _build_preview_workflow_id,
    _bulk_item_failure,
    _deploy_pipeline_disabled,
    _deploy_workflow_id,
    _deployment_target_from_input,
    _is_eligible_approver,
    _kick_validate_custom_domain,
    _lookup_deployment_by_token,
    _manifest_job_agents_only,
    _manual_preview_namespace,
    _migrate_workflow_id,
    _process_bulk_approve_one,
    _process_bulk_reject_one,
    _record_approval_vote_and_maybe_start,
    _record_workflow_run,
    _required_approvals_for,
    _resolve_app_env,
    _rollback_workflow_id,
    _self_approve_allowed,
    _slugify_branch,
    _start_deploy_workflow_on_commit,
    _start_extras,
    _teardown_workflow_id,
)
from astrolift_lifecycle.schema.mutations.maintenance import MaintenanceMutations
from astrolift_lifecycle.schema.mutations.migration import MigrationMutations
from astrolift_lifecycle.schema.mutations.previews import PreviewMutations
from astrolift_lifecycle.schema.mutations.recovery import RecoveryMutations
from astrolift_lifecycle.schema.mutations.tasks import TaskMutations

# Re-exported for the public import surface (tests / cross-app importers).
from astrolift_lifecycle.schema.mutations.types import (  # noqa: F401
    AbortDeploymentInput,
    AddAppDomainInput,
    AddWildcardDomainInput,
    ApproveByTokenInput,
    ArchiveAppRegistryRepoInput,
    BulkApproveDeploymentsInput,
    BulkDeploymentResultData,
    BulkDeploymentResultItem,
    BulkRejectDeploymentsInput,
    CancelDeregisterInput,
    CancelDeregisterPayload,
    CiSecretValidationType,
    ClearEnvironmentSettingInput,
    CreateDeployTokenInput,
    CreatePreviewEnvironmentInput,
    DeleteAppDnsRecordInput,
    DeleteAppIdentityRoleInput,
    DeleteAppIngressInput,
    DeploymentByIdInput,
    DeployTokenSecretReveal,
    DeregisterAppInput,
    DeregisterAppPayload,
    DomainPathRouteInput,
    DomainRedirectRuleInput,
    EnvironmentByIdInput,
    ExtendPreviewTtlInputGql,
    ForceRedeployInput,
    ForceRedeployPayload,
    InstallSourceWebhookInput,
    InstallSourceWebhookPayload,
    MigrateAppInputGql,
    PromoteDeploymentInput,
    PushCiSecretsPayload,
    PushCiSecretsToRepoInput,
    PushCiWorkflowPayload,
    PushCiWorkflowToRepoInput,
    ReapCloudOrphanInput,
    ReapCloudOrphanPayload,
    RecheckDomainValidationInput,
    RejectByTokenInput,
    RemoveAppDomainInput,
    RestartWorkloadInput,
    RetryAstroliftAutowireInput,
    RetryAstroliftAutowirePayload,
    RevokeAppCertificateInput,
    RevokeDeployTokenInput,
    RotateDeployTokenInput,
    RunJobOnceInput,
    RunJobOncePayload,
    RunTaskInput,
    ScaleWorkloadInput,
    SetDomainPathRoutesInput,
    SetDomainRedirectsInput,
    SetEnvironmentSettingInput,
    SetPreviewPinnedInput,
    StartDeploymentInput,
    TearDownPreviewInputGql,
    TriggerDeployWorkflowInput,
    TriggerDeployWorkflowPayload,
    UploadCustomDomainCertificateInput,
    ValidateAstroliftCiSecretsInput,
    ValidateAstroliftCiSecretsPayload,
    _AppDomainRemovedPayload,
    _CapabilityDeprovisionPayload,
    _DeployTokenRevokedPayload,
    _WorkloadOpPayload,
)


@strawberry.type
class LifecycleMutation(
    DeploymentMutations,
    EnvironmentMutations,
    PreviewMutations,
    MigrationMutations,
    DomainMutations,
    DeployTokenMutations,
    DeprovisionMutations,
    AppOpsMutations,
    DeregisterMutations,
    RecoveryMutations,
    EnvironmentSettingMutations,
    TaskMutations,
    MaintenanceMutations,
):
    """Root mutation type — inherits fields from each domain mixin."""
