import { gql } from "@apollo/client";

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
  registeredAppSlug
  environmentName
  createdAt
  updatedAt
  lastActionAt
  lastActionKind
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
