import { gql } from "@apollo/client";

/**
 * Set or update an app secret.
 *
 * ``input.scope`` (#679) controls which deploy targets see the value:
 *   - ``"all"`` — every environment (default)
 *   - ``"production"`` — only production deploys
 *   - ``"preview"`` — every preview deploy
 *   - ``"preview:<branch>"`` — only the named preview branch
 *
 * The field is non-nullable on the backend (defaults to ``"all"``);
 * call sites must always pass a value in ``variables.input.scope``.
 */
export const SET_APP_SECRET = gql`
  mutation SetAppSecret($input: SetAppSecretInput!) {
    setAppSecret(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
      }
      data {
        appSlug
        key
        rawManifestStaged
        pendingProposalId
      }
    }
  }
`;

/**
 * Rotate an app secret — same shape + behavior as setAppSecret but
 * emits action='app.secret.rotate' in the audit log so credential
 * rotations are filterable from generic edits (#726). FE renders a
 * Rotate button distinct from Edit on revealed-secret rows (#714).
 *
 * ``input.scope`` (#679) follows the same rules as setAppSecret — the
 * rotated value lands on the requested scope only.
 */
export const ROTATE_APP_SECRET = gql`
  mutation RotateAppSecret($input: RotateAppSecretInput!) {
    rotateAppSecret(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
      }
      data {
        appSlug
        key
        rawManifestStaged
        pendingProposalId
      }
    }
  }
`;

export const DELETE_APP_SECRET = gql`
  mutation DeleteAppSecret($input: DeleteAppSecretInput!) {
    deleteAppSecret(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        appSlug
        key
        rawManifestStaged
        pendingProposalId
      }
    }
  }
`;

export const BULK_IMPORT_APP_SECRETS = gql`
  mutation BulkImportAppSecrets($input: BulkImportAppSecretsInput!) {
    bulkImportAppSecrets(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        appSlug
        keysSet
        rawManifestStaged
      }
    }
  }
`;

export const ATTACH_SECRET_BUNDLE = gql`
  mutation AttachSecretBundle($input: AttachSecretBundleInput!) {
    attachSecretBundle(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        bundleSlug
        bundleName
        environmentName
        prefix
      }
    }
  }
`;

export const DETACH_SECRET_BUNDLE = gql`
  mutation DetachSecretBundle($input: DetachSecretBundleInput!) {
    detachSecretBundle(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        attachmentId
        deleted
        pendingProposalId
      }
    }
  }
`;

export const REVEAL_APP_SECRET = gql`
  mutation RevealAppSecret($input: RevealAppSecretInput!) {
    revealAppSecret(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        secretId
        key
        environmentName
        value
        revealedAt
      }
    }
  }
`;

const MANAGED_SERVICE_FIELDS = `
  id
  name
  kind
  variant
  status
  statusError
  config
  appliedConfig
  operationKind
  operationWorkflowId
  operationRunId
  operationStartedAt
  operationCompletedAt
  registeredAppSlug
  projectSlug
  ownerScope
  clusterSlug
  environmentName
  createdAt
  updatedAt
  lastActionAt
  lastActionKind
  editableFields
  attachments {
    id
    consumerKind
    consumerSlug
    environmentName
  }
`;

export const PROVISION_PROJECT_MANAGED_SERVICE = gql`
  mutation ProvisionProjectManagedService($input: ProvisionProjectManagedServiceInput!) {
    provisionProjectManagedService(input: $input) {
      ok
      errors { code message field }
      data { ${MANAGED_SERVICE_FIELDS} }
    }
  }
`;

export const ATTACH_PROJECT_MANAGED_SERVICE = gql`
  mutation AttachProjectManagedService($input: AttachProjectManagedServiceInput!) {
    attachProjectManagedService(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        consumerKind
        consumerSlug
        environmentName
      }
    }
  }
`;

export const DETACH_PROJECT_MANAGED_SERVICE = gql`
  mutation DetachProjectManagedService($input: DetachProjectManagedServiceInput!) {
    detachProjectManagedService(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        consumerKind
        consumerSlug
        environmentName
      }
    }
  }
`;

export const REPROVISION_PROJECT_MANAGED_SERVICE = gql`
  mutation ReprovisionProjectManagedService($input: ReprovisionManagedServiceInput!) {
    reprovisionProjectManagedService(input: $input) {
      ok
      errors { code message field }
      data { ${MANAGED_SERVICE_FIELDS} }
    }
  }
`;

export const DEPROVISION_PROJECT_MANAGED_SERVICE = gql`
  mutation DeprovisionProjectManagedService($input: DeprovisionManagedServiceInput!) {
    deprovisionProjectManagedService(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const CREATE_PROJECT_SECRET_BUNDLE = gql`
  mutation CreateProjectSecretBundle($input: CreateProjectSecretBundleInput!) {
    createProjectSecretBundle(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        backendRef
        projectSlug
        clusterSlug
        keyCount
        keyNames
        lastKnownKeysAt
        createdAt
      }
    }
  }
`;

export const SET_PROJECT_BUNDLE_SECRET = gql`
  mutation SetProjectBundleSecret($input: ProjectSecretBundleKeyInput!) {
    setProjectBundleSecretValue(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        backendRef
        projectSlug
        clusterSlug
        keyCount
        keyNames
        lastKnownKeysAt
        createdAt
      }
    }
  }
`;

export const DELETE_PROJECT_BUNDLE_SECRET = gql`
  mutation DeleteProjectBundleSecret($input: ProjectSecretBundleKeyInput!) {
    deleteProjectBundleSecretValue(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        backendRef
        projectSlug
        clusterSlug
        keyCount
        keyNames
        lastKnownKeysAt
        createdAt
      }
    }
  }
`;

export const REVEAL_PROJECT_BUNDLE_SECRET = gql`
  mutation RevealProjectBundleSecret($input: ProjectSecretBundleKeyInput!) {
    revealProjectBundleSecretValue(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        key
        value
        provider
        revealedAt
      }
    }
  }
`;

export const DELETE_PROJECT_SECRET_BUNDLE = gql`
  mutation DeleteProjectSecretBundle($bundleId: GUID!) {
    deleteProjectSecretBundle(bundleId: $bundleId) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        backendRef
        projectSlug
        clusterSlug
        keyCount
        keyNames
        lastKnownKeysAt
        createdAt
      }
    }
  }
`;

export const PROVISION_MANAGED_SERVICE = gql`
  mutation ProvisionManagedService($input: ProvisionManagedServiceInput!) {
    provisionManagedService(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${MANAGED_SERVICE_FIELDS}
      }
    }
  }
`;

export const UPDATE_MANAGED_SERVICE = gql`
  mutation UpdateManagedService($input: UpdateManagedServiceInput!) {
    updateManagedService(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${MANAGED_SERVICE_FIELDS}
      }
    }
  }
`;

/**
 * One bounded chat completion against a hosted vLLM model (#2064), relayed
 * through the cluster's in-cluster keep-alive agent -- the control plane
 * never reaches the model directly. Blocks until the agent's reply lands or
 * the bounded wait gives up (``status: "timed_out"``); there is no polling
 * on the client side.
 */
export const TEST_MODEL_ENDPOINT = gql`
  mutation TestModelEndpoint($input: TestModelEndpointInput!) {
    testModelEndpoint(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        status
        reply
        latencyMs
        promptTokens
        completionTokens
        totalTokens
        error
      }
    }
  }
`;

/**
 * Trigger a full reprovision cycle for a managed service (#745).
 *
 * Use when a config change requires tearing down the backing cloud
 * resource before re-creating it — i.e., the changed key is NOT in
 * ``editableFields``. The service status transitions to PENDING; the
 * lifecycle workflow loop picks it up.
 */
export const REPROVISION_MANAGED_SERVICE = gql`
  mutation ReprovisionManagedService($input: ReprovisionManagedServiceInput!) {
    reprovisionManagedService(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${MANAGED_SERVICE_FIELDS}
      }
    }
  }
`;

export const DEPROVISION_MANAGED_SERVICE = gql`
  mutation DeprovisionManagedService($input: DeprovisionManagedServiceInput!) {
    deprovisionManagedService(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const REVEAL_MANAGED_SERVICE_CONNECTION = gql`
  mutation RevealManagedServiceConnection($input: RevealManagedServiceConnectionInput!) {
    revealManagedServiceConnection(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        managedServiceId
        kind
        name
        environmentName
        connectionSecretRef
        revealedAt
        keys {
          key
          value
          isSecret
        }
      }
    }
  }
`;

// #488 Secret-change approval workflow mutations ----------------------

const SECRET_PROPOSAL_FIELDS = `
  id
  registeredAppSlug
  environmentName
  op
  status
  proposerUserId
  proposerDisplayName
  payload
  payloadDiff
  requiredApproverCount
  approvalsCount
  expiresAt
  decidedAt
  appliedAt
  applyError
  createdAt
  approvals {
    id
    approverUserId
    approverDisplayName
    decision
    decidedAt
    reason
  }
`;

export const PROPOSE_SECRET_CHANGE = gql`
  mutation ProposeSecretChange($input: ProposeSecretChangeInput!) {
    proposeSecretChange(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${SECRET_PROPOSAL_FIELDS}
      }
    }
  }
`;

export const APPROVE_SECRET_CHANGE = gql`
  mutation ApproveSecretChange($input: ApproveSecretChangeInput!) {
    approveSecretChange(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${SECRET_PROPOSAL_FIELDS}
      }
    }
  }
`;

export const REJECT_SECRET_CHANGE = gql`
  mutation RejectSecretChange($input: RejectSecretChangeInput!) {
    rejectSecretChange(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${SECRET_PROPOSAL_FIELDS}
      }
    }
  }
`;

export const WITHDRAW_SECRET_CHANGE = gql`
  mutation WithdrawSecretChange($input: WithdrawSecretChangeInput!) {
    withdrawSecretChange(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${SECRET_PROPOSAL_FIELDS}
      }
    }
  }
`;

// Email suppression list (#631) ---------------------------------------

export const ADD_EMAIL_SUPPRESSION_ENTRY = gql`
  mutation AddEmailSuppressionEntry($input: AddEmailSuppressionEntryInput!) {
    addEmailSuppressionEntry(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        address
        reason
      }
    }
  }
`;

export const REMOVE_EMAIL_SUPPRESSION_ENTRY = gql`
  mutation RemoveEmailSuppressionEntry($input: RemoveEmailSuppressionEntryInput!) {
    removeEmailSuppressionEntry(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        address
        removed
      }
    }
  }
`;

// Email template CRUD (#635) ------------------------------------------

const EMAIL_TEMPLATE_FIELDS = `
  name
  subject
  htmlBody
  textBody
  createdAt
`;

export const CREATE_EMAIL_TEMPLATE = gql`
  mutation CreateEmailTemplate($input: CreateEmailTemplateInput!) {
    createEmailTemplate(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${EMAIL_TEMPLATE_FIELDS}
      }
    }
  }
`;

export const UPDATE_EMAIL_TEMPLATE = gql`
  mutation UpdateEmailTemplate($input: UpdateEmailTemplateInput!) {
    updateEmailTemplate(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${EMAIL_TEMPLATE_FIELDS}
      }
    }
  }
`;

export const DELETE_EMAIL_TEMPLATE = gql`
  mutation DeleteEmailTemplate($input: DeleteEmailTemplateInput!) {
    deleteEmailTemplate(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const SEND_MANAGED_SERVICE_TEST_EMAIL = gql`
  mutation SendManagedServiceTestEmail($input: SendManagedServiceTestEmailInput!) {
    sendManagedServiceTestEmail(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        managedServiceId
        recipient
        subject
        sentAt
        transport
      }
    }
  }
`;
