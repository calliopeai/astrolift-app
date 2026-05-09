import { gql } from "@apollo/client";

const APP_DOMAIN_FIELDS = `
  id
  hostname
  certState
  validationMethod
  validationToken
  lastCheckedAt
  isActive
  registeredAppSlug
  createdAt
`;

export const ADD_APP_DOMAIN = gql`
  mutation AddAppDomain($input: AddAppDomainInput!) {
    addAppDomain(input: $input) {
      ok
      errors { code message field }
      data { ${APP_DOMAIN_FIELDS} }
    }
  }
`;

export const REMOVE_APP_DOMAIN = gql`
  mutation RemoveAppDomain($input: RemoveAppDomainInput!) {
    removeAppDomain(input: $input) {
      ok
      errors { code message }
      data { id deleted }
    }
  }
`;

export const RECHECK_DOMAIN_VALIDATION = gql`
  mutation RecheckDomainValidation($input: RecheckDomainValidationInput!) {
    recheckDomainValidation(input: $input) {
      ok
      errors { code message }
      data { ${APP_DOMAIN_FIELDS} }
    }
  }
`;

const DEPLOY_TOKEN_FIELDS = `
  id
  name
  last4
  scopes
  expiresAt
  lastUsedAt
  isRevoked
  lastRotatedAt
  registeredAppSlug
  createdAt
`;

export const CREATE_DEPLOY_TOKEN = gql`
  mutation CreateDeployToken($input: CreateDeployTokenInput!) {
    createDeployToken(input: $input) {
      ok
      errors { code message field }
      data {
        token { ${DEPLOY_TOKEN_FIELDS} }
        plaintextSecret
      }
    }
  }
`;

export const ROTATE_DEPLOY_TOKEN = gql`
  mutation RotateDeployToken($input: RotateDeployTokenInput!) {
    rotateDeployToken(input: $input) {
      ok
      errors { code message }
      data {
        token { ${DEPLOY_TOKEN_FIELDS} }
        plaintextSecret
      }
    }
  }
`;

export const REVOKE_DEPLOY_TOKEN = gql`
  mutation RevokeDeployToken($input: RevokeDeployTokenInput!) {
    revokeDeployToken(input: $input) {
      ok
      errors { code message }
      data { id revoked }
    }
  }
`;

const DEPLOYMENT_FIELDS = `
  id
  registeredAppSlug
  environmentName
  workloadSlug
  triggerKind
  status
  imageTag
  imageDigest
  clusterRevision
  approvalsRequired
  approvalsReceived
  startedAt
  succeededAt
  failedAt
  endedAt
  durationSeconds
  createdAt
`;

export const START_DEPLOYMENT = gql`
  mutation StartDeployment($input: StartDeploymentInput!) {
    startDeployment(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${DEPLOYMENT_FIELDS}
      }
    }
  }
`;

export const APPROVE_DEPLOYMENT = gql`
  mutation ApproveDeployment($input: DeploymentByIdInput!) {
    approveDeployment(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${DEPLOYMENT_FIELDS}
      }
    }
  }
`;

export const ABORT_DEPLOYMENT = gql`
  mutation AbortDeployment($input: DeploymentByIdInput!) {
    abortDeployment(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${DEPLOYMENT_FIELDS}
      }
    }
  }
`;

export const ROLLBACK_DEPLOYMENT = gql`
  mutation RollbackDeployment($input: DeploymentByIdInput!) {
    rollbackDeployment(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${DEPLOYMENT_FIELDS}
      }
    }
  }
`;

export const REDEPLOY_APP = gql`
  mutation RedeployApp($input: DeploymentByIdInput!) {
    redeployApp(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${DEPLOYMENT_FIELDS}
      }
    }
  }
`;

export const TEAR_DOWN_PREVIEW = gql`
  mutation TearDownPreview($input: TearDownPreviewInputGql!) {
    tearDownPreview(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${DEPLOYMENT_FIELDS}
      }
    }
  }
`;

const ENVIRONMENT_FIELDS = `
  id
  name
  url
  deploysPaused
  requiredApprovals
  registeredAppSlug
  clusterSlug
  domainZone
  createdAt
`;

export const PAUSE_ENVIRONMENT = gql`
  mutation PauseEnvironment($input: EnvironmentByIdInput!) {
    pauseEnvironment(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${ENVIRONMENT_FIELDS}
      }
    }
  }
`;

export const RESUME_ENVIRONMENT = gql`
  mutation ResumeEnvironment($input: EnvironmentByIdInput!) {
    resumeEnvironment(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${ENVIRONMENT_FIELDS}
      }
    }
  }
`;
