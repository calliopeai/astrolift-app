import { gql } from "@apollo/client";

export const CONNECT_SOURCE = gql`
  mutation ConnectSource($input: ConnectSourceInput!) {
    connectSource(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        kind
        name
        displayName
        accountLogin
        repoVisibilityScopes
        isOauthAppConfig
        isActive
        createdAt
      }
    }
  }
`;

export const CONNECT_EXISTING_GITHUB_APP = gql`
  mutation ConnectExistingGithubApp($input: ConnectExistingGithubAppInput!) {
    connectExistingGithubApp(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        kind
        name
        displayName
        accountLogin
        installationId
        isActive
        createdAt
      }
    }
  }
`;

export const UPDATE_SOURCE_CONNECTION = gql`
  mutation UpdateSourceConnection($input: UpdateSourceConnectionInput!) {
    updateSourceConnection(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        displayName
        repoVisibilityScopes
        isActive
        appClientId
        needsClientId
      }
    }
  }
`;

export const DISCONNECT_SOURCE = gql`
  mutation DisconnectSource($input: DisconnectSourceInput!) {
    disconnectSource(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        isActive
      }
    }
  }
`;

export const GENERATE_SSH_DEPLOY_KEY = gql`
  mutation GenerateSshDeployKey($input: GenerateSshDeployKeyInput!) {
    generateSshDeployKey(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        key {
          id
          name
          publicKey
          fingerprintSha256
          registeredAppSlug
          createdAt
        }
      }
    }
  }
`;

export const ROTATE_WEBHOOK_SECRET = gql`
  mutation RotateWebhookSecret($input: RotateWebhookSecretInput!) {
    rotateWebhookSecret(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        connectionId
        plaintextSecret
        webhookUrlPath
      }
    }
  }
`;

export const DELETE_SSH_DEPLOY_KEY = gql`
  mutation DeleteSshDeployKey($input: DeleteSshDeployKeyInput!) {
    deleteSshDeployKey(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
      }
    }
  }
`;

export const PUSH_CI_WORKFLOW = gql`
  mutation PushCiWorkflow($input: PushCiWorkflowInput!) {
    pushCiWorkflow(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        commitSha
        filePath
        repoUrl
      }
    }
  }
`;

// Managed CI-workflow drift actions (#1210). All three return the
// AstroliftCiWorkflowSyncStatus so the caller can re-render the drift
// badge straight from the mutation response. Fields are inlined (not
// interpolated) so this file stays parseable by graphql-codegen's
// document loader.

export const RESYNC_CI_WORKFLOW = gql`
  mutation ResyncAstroliftCiWorkflow($input: CiWorkflowSyncActionInput!) {
    resyncAstroliftCiWorkflow(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        state
        syncedTemplateVersion
        currentTemplateVersion
        syncedAt
        checkedAt
        path
        prUrl
        detail
      }
    }
  }
`;

export const ADOPT_REPO_CI_WORKFLOW = gql`
  mutation AdoptRepoCiWorkflow($input: CiWorkflowSyncActionInput!) {
    adoptRepoCiWorkflow(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        state
        syncedTemplateVersion
        currentTemplateVersion
        syncedAt
        checkedAt
        path
        prUrl
        detail
      }
    }
  }
`;

export const REFRESH_CI_WORKFLOW_SYNC_STATUS = gql`
  mutation RefreshCiWorkflowSyncStatus($input: CiWorkflowSyncActionInput!) {
    refreshCiWorkflowSyncStatus(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        state
        syncedTemplateVersion
        currentTemplateVersion
        syncedAt
        checkedAt
        path
        prUrl
        detail
      }
    }
  }
`;
