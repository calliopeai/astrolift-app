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

export const UPDATE_SOURCE_CONNECTION = gql`
  mutation UpdateSourceConnection($input: UpdateSourceConnectionInput!) {
    updateSourceConnection(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        displayName
        repoVisibilityScopes
        isActive
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
