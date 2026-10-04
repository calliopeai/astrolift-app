"""ServicesMutation — assembled from per-feature mixin modules."""

from __future__ import annotations

import strawberry

from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations
from astrolift_services.schema.hf_connections import HuggingFaceConnectionsMutation
from astrolift_services.schema.local_model_artifacts import ModelArtifactsMutation
from astrolift_services.schema.model_connections import ModelConnectionsMutation
from astrolift_services.schema.mutations.bundles import SecretBundleMutations
from astrolift_services.schema.mutations.email import EmailServiceMutations
from astrolift_services.schema.mutations.helpers import (  # noqa: F401
    _DEFAULT_PROPOSAL_TTL_SECONDS,
    _ENV_NAME_HINT,
    _PUBLIC_ENVELOPE_KEY_SUFFIXES,
    _VALID_SECRET_SOURCES,
    _actor_user,
    _app_secret_target_from_input,
    _caller_org_id,
    _client_ip,
    _is_eligible_secret_approver,
    _is_envelope_key_public,
    _maybe_create_proposal_for_write,
    _proposal_target_from_input,
    _proposal_ttl_seconds,
    _resolve_email_service,
    _self_approve_secrets_allowed,
    _stage_manifest,
    _upsert_app_secret_metadata,
    _validate_env_key,
)
from astrolift_services.schema.mutations.managed_services import ManagedServiceMutations
from astrolift_services.schema.mutations.secret_changes import SecretChangeMutations
from astrolift_services.schema.mutations.secrets import SecretMutations

# Re-exported for the public import surface (tests / cross-app importers).
from astrolift_services.schema.mutations.types import (  # noqa: F401
    AddEmailSuppressionEntryInput,
    ApproveSecretChangeInput,
    AttachProjectManagedServiceInput,
    AttachSecretBundleInput,
    BulkImportAppSecretsInput,
    CreateEmailTemplateInput,
    CreateProjectSecretBundleInput,
    DeleteAppSecretInput,
    DeleteEmailTemplateInput,
    DeprovisionManagedServiceInput,
    DetachProjectManagedServiceInput,
    DetachSecretBundleInput,
    ProjectSecretBundleKeyInput,
    ProposeSecretChangeInput,
    ProvisionManagedServiceInput,
    ProvisionProjectManagedServiceInput,
    RejectSecretChangeInput,
    RemoveEmailSuppressionEntryInput,
    ReprovisionManagedServiceInput,
    RevealAppSecretInput,
    RevealManagedServiceConnectionInput,
    RotateAppSecretInput,
    RotateSecretBundleInput,
    SendManagedServiceTestEmailInput,
    SetAppSecretInput,
    SetAppSecretMetadataInput,
    TestModelEndpointInput,
    UpdateEmailTemplateInput,
    UpdateManagedServiceInput,
    UpdateProjectSecretBundleInput,
    WithdrawSecretChangeInput,
    _AppSecretMetadataPayload,
    _AppSecretWritePayload,
    _AttachmentRemovedPayload,
    _BulkImportPayload,
    _EmailSuppressionAddPayload,
    _EmailSuppressionRemovePayload,
    _EmailTemplateDeletedPayload,
    _ManagedServiceDeletedPayload,
)


@strawberry.type
class ServicesMutation(
    ClusterModelMutations,
    ModelConnectionsMutation,
    HuggingFaceConnectionsMutation,
    ModelArtifactsMutation,
    SecretMutations,
    SecretBundleMutations,
    ManagedServiceMutations,
    EmailServiceMutations,
    SecretChangeMutations,
):
    """Root mutation type — inherits fields from each domain mixin."""
