import { gql } from "@apollo/client";

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
