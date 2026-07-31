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
  appClientId
  needsClientId
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

/**
 * Cursor-paginated companions to ``LIST_SOURCE_CONNECTIONS`` /
 * ``LIST_SSH_DEPLOY_KEYS`` (#1230). ``{ items, nextCursor, totalCount }``
 * out, ``limit`` + ``after`` in, so ``useCursorTable`` walks the whole list
 * on the server.
 *
 * ``appSlug`` on the deploy-key page stays optional and keeps the same
 * meaning it has on the list field: ``null`` is the org-wide (app-less) key
 * set the Source providers screen shows, a slug scopes to one app. Pass it
 * explicitly — omitting the variable and passing it as ``null`` are the same
 * request here, but the surfaces that refetch both want one spelling.
 * Neither field takes a sort argument, so no ``sortVariable`` /
 * ``Column.sortKey``.
 *
 * These documents interpolate the file's field-list constants rather than
 * spreading named fragments because every other document here does, and the
 * file is excluded from the codegen document set for exactly that reason
 * (see ``codegen.ts``). Converting all of them together — and dropping the
 * exclusion — is a separate change.
 */
export const LIST_SOURCE_CONNECTIONS_PAGE = gql`
  query ListSourceConnectionsPage($search: String, $limit: Int, $after: String) {
    astroliftSourceConnectionsPage(search: $search, limit: $limit, after: $after) {
      items {
        ${CONNECTION_FIELDS}
      }
      nextCursor
      totalCount
    }
  }
`;

export const LIST_SSH_DEPLOY_KEYS_PAGE = gql`
  query ListSshDeployKeysPage(
    $appSlug: String
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftSshDeployKeysPage(
      appSlug: $appSlug
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ${SSH_KEY_FIELDS}
      }
      nextCursor
      totalCount
    }
  }
`;

export const GET_SOURCE_FILE = gql`
  query GetSourceFile(
    $connectionId: String!
    $repoFullName: String!
    $path: String!
    $ref: String!
  ) {
    astroliftSourceFile(
      connectionId: $connectionId
      repoFullName: $repoFullName
      path: $path
      ref: $ref
    ) {
      repoFullName
      path
      ref
      content
      errorCode
      errorMessage
      recoverable
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
