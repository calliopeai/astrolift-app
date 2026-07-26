"""RegistryMutation — assembled from per-feature mixin modules."""

from __future__ import annotations

import strawberry

from astrolift_registry.schema.mutations.app_settings import AppSettingMutations
from astrolift_registry.schema.mutations.apps import AppMutations
from astrolift_registry.schema.mutations.archive import ArchiveMutations
from astrolift_registry.schema.mutations.helpers import (  # noqa: F401
    _actor,
    _bootstrap_app_environments,
    _downgrade_to_deployer,
    _ensure_owner_access,
    _generate_unique_app_slug,
    _normalize_build_args,
    _resolve_approval_inputs,
    _validate_build_mode,
    _validate_build_strategy,
    _validate_effective_approval_policy,
    _viewer_can_access_project,
)
from astrolift_registry.schema.mutations.manifest import ManifestMutations
from astrolift_registry.schema.mutations.registration import RegistrationMutations
from astrolift_registry.schema.mutations.team_access import TeamAccessMutations

# Re-exported for the public import surface (tests / cross-app importers).
from astrolift_registry.schema.mutations.types import (  # noqa: F401
    ArchiveAppInput,
    AssignAppToProjectInput,
    GrantTeamAccessInput,
    MoveAppToTeamInput,
    PauseAppWebhookDeploysInput,
    PushManifestToRepoInput,
    RegisterAgentRepoInput,
    RegisterAgentRepoResultType,
    RegisterAppInput,
    RegisterAppRepoInput,
    RegisterAppRepoResultType,
    RegisteredAgentType,
    RegisteredAppEntryType,
    RestoreAppInput,
    ResumeAppWebhookDeploysInput,
    ResyncManifestFromRepoInput,
    ResyncManifestPayload,
    RevokeTeamAccessInput,
    SetAppSubdomainInput,
    SetRetentionPolicyInput,
    SoftDeleteAppInput,
    SyncManifestFromRepoInput,
    TearDownAppInput,
    TransferAppInput,
    UpdateAppInput,
    UpdateManifestInput,
    UpdateSecurityPolicyInput,
    _ManifestPushPayload,
    _ManifestStagePayload,
    _SoftDeletePayload,
)


@strawberry.type
class RegistryMutation(
    RegistrationMutations,
    AppMutations,
    ManifestMutations,
    TeamAccessMutations,
    AppSettingMutations,
    ArchiveMutations,
):
    """Root mutation type — inherits fields from each domain mixin."""
