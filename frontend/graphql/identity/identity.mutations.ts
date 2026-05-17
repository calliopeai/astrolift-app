import { gql } from "@apollo/client";

export const CREATE_TEAM = gql`
  mutation CreateTeam($input: CreateTeamInput!) {
    createTeam(input: $input) {
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
        organization {
          id
          slug
          name
        }
        createdAt
        updatedAt
        deletedAt
      }
    }
  }
`;

export const CREATE_PROJECT = gql`
  mutation CreateProject($input: CreateProjectInput!) {
    createProject(input: $input) {
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
        organization {
          id
          slug
          name
        }
        team {
          id
          slug
          name
        }
        createdAt
        updatedAt
        deletedAt
      }
    }
  }
`;

export const SOFT_DELETE_TEAM = gql`
  mutation SoftDeleteTeam($input: SoftDeleteByGuidInput!) {
    softDeleteTeam(input: $input) {
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

export const SOFT_DELETE_PROJECT = gql`
  mutation SoftDeleteProject($input: SoftDeleteByGuidInput!) {
    softDeleteProject(input: $input) {
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

export const UPDATE_ORGANIZATION = gql`
  mutation UpdateOrganization($input: UpdateOrganizationInput!) {
    updateOrganization(input: $input) {
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
        website
        scimEnabled
        auditLogRetentionDays
        previewMaxActiveDefault
        logRetentionDaysDefault
        allowUserProfileEdit
      }
    }
  }
`;

export const UPDATE_MY_PROFILE = gql`
  mutation UpdateMyProfile($input: UpdateMyProfileInput!) {
    updateMyProfile(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        userId
        username
        firstName
        lastName
        email
        lockedFields
        orgAllowsEdit
      }
    }
  }
`;

export const MARK_ONBOARDING_COMPLETE = gql`
  mutation MarkOnboardingComplete($input: MarkOnboardingCompleteInput!) {
    markOnboardingComplete(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        alreadyCompleted
        organization {
          id
          slug
          name
          onboardingCompletedAt
        }
      }
    }
  }
`;

export const LOGOUT_ALL_SESSIONS = gql`
  mutation LogoutAllSessions($input: LogoutAllSessionsInput!) {
    logoutAllSessions(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        revokedCount
        keptCurrent
      }
    }
  }
`;

export const REVOKE_ASTROLIFT_SESSION = gql`
  mutation RevokeAstroliftSession($input: RevokeAstroliftSessionInput!) {
    revokeAstroliftSession(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        revoked
      }
    }
  }
`;

export const HEARTBEAT_SESSION = gql`
  mutation HeartbeatSession {
    heartbeatSession {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        lastSeenAt
      }
    }
  }
`;

export const CREATE_POLICY = gql`
  mutation CreatePolicy($input: CreatePolicyInput!) {
    createPolicy(input: $input) {
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
        scopeLevel
        effect
        actionPattern
      }
    }
  }
`;

export const UPDATE_POLICY = gql`
  mutation UpdatePolicy($input: UpdatePolicyInput!) {
    updatePolicy(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
      }
      data {
        id
        slug
        name
        effect
        actionPattern
        conditions
        version
      }
    }
  }
`;

export const SOFT_DELETE_POLICY = gql`
  mutation SoftDeletePolicy($input: SoftDeleteByGuidInput!) {
    softDeletePolicy(input: $input) {
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

export const CREATE_IDENTITY_PROVIDER = gql`
  mutation CreateIdentityProvider($input: CreateIdentityProviderInput!) {
    createIdentityProvider(input: $input) {
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
        clientId
        oidcDiscoveryUrl
        metadataUrl
        isActive
      }
    }
  }
`;

export const UPDATE_IDENTITY_PROVIDER = gql`
  mutation UpdateIdentityProvider($input: UpdateIdentityProviderInput!) {
    updateIdentityProvider(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
      }
      data {
        id
        kind
        name
        clientId
        oidcDiscoveryUrl
        metadataUrl
        isActive
        version
      }
    }
  }
`;

export const SET_ACTIVE_IDENTITY_PROVIDER = gql`
  mutation SetActiveIdentityProvider($input: SetActiveIdentityProviderInput!) {
    setActiveIdentityProvider(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        kind
        name
        isActive
      }
    }
  }
`;

export const SOFT_DELETE_IDENTITY_PROVIDER = gql`
  mutation SoftDeleteIdentityProvider($input: SoftDeleteByGuidInput!) {
    softDeleteIdentityProvider(input: $input) {
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

export const CREATE_API_TOKEN = gql`
  mutation CreateApiToken($input: CreateApiTokenInput!) {
    createApiToken(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        plaintext
        apiToken {
          id
          name
          tokenLast4
          scopes
          expiresAt
          lastUsedAt
          lastUsedIp
          lastUsedAgent
          createdAt
          isRevoked
        }
      }
    }
  }
`;

export const REVOKE_API_TOKEN = gql`
  mutation RevokeApiToken($input: RevokeApiTokenInput!) {
    revokeApiToken(input: $input) {
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

export const GRANT_ROLE = gql`
  mutation GrantRole($input: GrantRoleInput!) {
    grantRole(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        user {
          id
          username
          email
        }
        role {
          id
          slug
          name
          scopeLevel
        }
        scopeKind
        scopeId
        sourceScopeLabel
        grantedAt
      }
    }
  }
`;

export const REVOKE_ROLE_BINDING = gql`
  mutation RevokeRoleBinding($input: RevokeRoleBindingInput!) {
    revokeRoleBinding(input: $input) {
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

const BULK_OP_RESULT_FIELDS = `
  results {
    id
    ok
    alreadyExisted
    errors {
      code
      message
      field
    }
  }
`;

export const BULK_REVOKE_ROLE_BINDINGS = gql`
  mutation BulkRevokeAstroliftRoleBindings($input: BulkRevokeRoleBindingsInput!) {
    bulkRevokeAstroliftRoleBindings(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${BULK_OP_RESULT_FIELDS}
        revokedCount
        failedCount
      }
    }
  }
`;

export const BULK_ASSIGN_TEAM_MEMBER_ROLES = gql`
  mutation BulkAssignAstroliftTeamMemberRoles(
    $input: BulkAssignTeamMemberRolesInput!
  ) {
    bulkAssignAstroliftTeamMemberRoles(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${BULK_OP_RESULT_FIELDS}
        assignedCount
        alreadyAssignedCount
        failedCount
      }
    }
  }
`;

const INVITATION_FIELDS = `
  id
  email
  scopeKind
  scopeId
  roleSlug
  status
  expiresAt
  acceptedAt
  invitedByUsername
  createdAt
`;

export const CREATE_INVITATION = gql`
  mutation CreateInvitation($input: CreateInvitationInput!) {
    createInvitation(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        invitation {
          ${INVITATION_FIELDS}
        }
        plaintextToken
        acceptUrlPath
      }
    }
  }
`;

export const REVOKE_INVITATION = gql`
  mutation RevokeInvitation($input: RevokeInvitationInput!) {
    revokeInvitation(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${INVITATION_FIELDS}
      }
    }
  }
`;

export const ADD_ORGANIZATION_ALLOWLIST_DOMAIN = gql`
  mutation AddOrganizationAllowlistDomain($input: AddOrganizationAllowlistDomainInput!) {
    addOrganizationAllowlistDomain(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        domain
        defaultRoleSlug
        requiresReview
        createdAt
        updatedAt
      }
    }
  }
`;

export const REMOVE_ORGANIZATION_ALLOWLIST_DOMAIN = gql`
  mutation RemoveOrganizationAllowlistDomain($input: RemoveOrganizationAllowlistDomainInput!) {
    removeOrganizationAllowlistDomain(input: $input) {
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

export const ACCEPT_INVITATION = gql`
  mutation AcceptInvitation($input: AcceptInvitationInput!) {
    acceptInvitation(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${INVITATION_FIELDS}
      }
    }
  }
`;

export const CONNECT_USER_SOURCE_PROVIDER = gql`
  mutation ConnectUserSourceProvider($input: ConnectUserSourceProviderInput!) {
    astroliftConnectUserSourceProvider(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        providerConfigId
        authorizationUrl
      }
    }
  }
`;

export const DISCONNECT_USER_SOURCE_PROVIDER = gql`
  mutation DisconnectUserSourceProvider($input: DisconnectUserSourceProviderInput!) {
    astroliftDisconnectUserSourceProvider(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        providerConfigId
        disconnectedId
      }
    }
  }
`;

// #487 — step-up auth. Elevate the session for a configurable
// short window (default 15 minutes, server-clamped) so sensitive
// mutations gated by ``@requires_elevation`` can run.
export const ELEVATE_ADMIN_SESSION = gql`
  mutation ElevateAdminSession($input: ElevateAdminSessionInput!) {
    elevateAdminSession(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        elevatedUntil
        secondsRemaining
        method
      }
    }
  }
`;

export const DEELEVATE_ADMIN_SESSION = gql`
  mutation DeelevateAdminSession {
    deelevateAdminSession {
      ok
      errors {
        code
        message
        field
      }
      data {
        previouslyElevated
      }
    }
  }
`;

// #494 — mobile install enrollment QR. Operator clicks "Pair new
// device" → this mutation mints a pre-approved device-flow session
// and returns the QR (rendered server-side as SVG to avoid a
// frontend QR-encoder dependency) + the payload mobile scanners
// decode. Single-use; rate-limited per operator.
export const GENERATE_INSTALL_ENROLLMENT_QR = gql`
  mutation GenerateInstallEnrollmentQr($input: GenerateInstallEnrollmentQrInput!) {
    generateInstallEnrollmentQr(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        qrPayload
        qrSvg
        verificationUri
        sessionId
        sessionGuid
        expiresAt
      }
    }
  }
`;
