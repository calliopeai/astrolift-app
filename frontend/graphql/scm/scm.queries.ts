import { gql } from "@apollo/client";

const CONNECTION_FIELDS = `
  id
  kind
  name
  displayName
  accountLogin
  installationId
  apiBaseUrl
  oauthClientId
  oauthRedirectUri
  repoVisibilityScopes
  isOauthAppConfig
  isActive
  tokenExpiresAt
  lastUsedAt
  createdAt
  updatedAt
  isPersonal
  userUsername
  parentOauthAppId
`;

const SSH_KEY_FIELDS = `
  id
  name
  publicKey
  fingerprintSha256
  registeredAppSlug
  lastUsedAt
  isActive
  createdAt
`;

export const LIST_SOURCE_CONNECTIONS = gql`
  query ListSourceConnections {
    astroliftSourceConnections {
      ${CONNECTION_FIELDS}
    }
  }
`;

export const LIST_SSH_DEPLOY_KEYS = gql`
  query ListSshDeployKeys($appSlug: String) {
    astroliftSshDeployKeys(appSlug: $appSlug) {
      ${SSH_KEY_FIELDS}
    }
  }
`;

export const LIST_AVAILABLE_REPOS = gql`
  query ListAvailableRepos(
    $connectionId: String!
    $search: String
    $limit: Int
  ) {
    astroliftAvailableRepos(
      connectionId: $connectionId
      search: $search
      limit: $limit
    ) {
      repos {
        fullName
        name
        description
        defaultBranch
        visibility
        cloneUrlHttps
        cloneUrlSsh
        webUrl
        isArchived
        isFork
        pushedAt
      }
      errorCode
      errorMessage
      recoverable
    }
  }
`;
