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
  txtChallengeToken
  expectedCnameTarget
  isPlatformManagedZone
  lastValidationError
  certificateState
  lastCertificateError
  byoCertificateUploadedAt
  requiredDnsRecords {
    kind
    name
    value
    ttl
    propagated
    lastCheckedAt
    message
  }
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

export const RECHECK_DOMAIN_VALIDATION = gql`
  mutation RecheckDomainValidation($input: RecheckDomainValidationInput!) {
    recheckDomainValidation(input: $input) {
      ok
      errors { code message }
      data { ${APP_DOMAIN_FIELDS} }
    }
  }
`;

export const UPLOAD_CUSTOM_DOMAIN_CERTIFICATE = gql`
  mutation UploadCustomDomainCertificate(
    $input: UploadCustomDomainCertificateInput!
  ) {
    uploadCustomDomainCertificate(input: $input) {
      ok
      errors { code message field }
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
      errors {
        code
        message
      }
      data {
        id
        revoked
      }
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
  ingressPaused
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

export const PAUSE_APP_INGRESS = gql`
  mutation PauseAppIngress($input: EnvironmentByIdInput!) {
    pauseAppIngress(input: $input) {
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

export const RESUME_APP_INGRESS = gql`
  mutation ResumeAppIngress($input: EnvironmentByIdInput!) {
    resumeAppIngress(input: $input) {
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

// --- Rebuild & deploy via CI workflow dispatch (#387) -------------
//
// Fires the source host's workflow-dispatch endpoint for the app's
// astrolift-ci.yml workflow on the configured deploy branch. The
// payload's runUrl points at the workflow's runs page (GitHub's
// dispatch endpoint returns 204 with no run id) — the toast links
// to it so the operator can watch the new run come up.

export const TRIGGER_DEPLOY_WORKFLOW = gql`
  mutation TriggerDeployWorkflow($input: TriggerDeployWorkflowInput!) {
    triggerAstroliftDeployWorkflow(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        runUrl
        dispatchedBranch
      }
    }
  }
`;

// --- Live workload ops (#388) -------------------------------------
//
// Direct-to-cluster operational actions on a Workload's Deployment.
// Both are gated on `app.deploy` server-side; the FE additionally
// gates the buttons with <Can permission="app.deploy">. The
// `WORKLOAD_OP_FIELDS` are deliberately small — the read-back is
// best-effort; the FE refetches the workload list to converge the
// displayed counters.

const WORKLOAD_OP_FIELDS = `
  workloadId
  newRevision
  desiredReplicas
  readyReplicas
`;

export const RESTART_WORKLOAD = gql`
  mutation RestartAstroliftWorkload($input: RestartWorkloadInput!) {
    restartAstroliftWorkload(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${WORKLOAD_OP_FIELDS}
      }
    }
  }
`;

export const SCALE_WORKLOAD = gql`
  mutation ScaleAstroliftWorkload($input: ScaleWorkloadInput!) {
    scaleAstroliftWorkload(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${WORKLOAD_OP_FIELDS}
      }
    }
  }
`;

// --- Install source-host push webhook (#385) -----------------------
//
// Registers (or refreshes) the push-event webhook on the app's
// configured source repo pointing at the platform's receiver URL.
// Status comes back as either ``created`` (a brand-new hook landed)
// or ``refreshed`` (same URL was already registered, or the App's
// own webhook covers this repo; the shared HMAC secret rotates and
// ``installedAt`` advances either way).

export const INSTALL_SOURCE_WEBHOOK = gql`
  mutation InstallAstroliftSourceWebhook($input: InstallSourceWebhookInput!) {
    installAstroliftSourceWebhook(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        status
        hookId
        receiverUrl
      }
    }
  }
`;

// --- Push astrolift-ci.yml to the source repo (#384) --------------
//
// Renders the canonical CI workflow against the app's persisted state
// (slug, deploy branch, ECR URI, push-role ARN, platform API URL) and
// commits it to ``.github/workflows/astrolift-ci.yml`` on the deploy
// branch. Idempotent when the file already matches the rendered
// template; falls back to a side-branch + PR when the deploy branch
// is protected. Paired with ``triggerAstroliftDeployWorkflow`` (#387)
// so the operator can go file-missing → first-deploy in two clicks.

export const PUSH_CI_WORKFLOW_TO_REPO = gql`
  mutation PushAstroliftCiWorkflowToRepo($input: PushCiWorkflowToRepoInput!) {
    pushAstroliftCiWorkflowToRepo(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        status
        commitSha
        prUrl
      }
    }
  }
`;

// --- Push CI secrets to repo (#383) --------------------------------
//
// Seals the five `ASTROLIFT_*` GitHub Actions secret values with the
// repo's public key and PUTs them via the viewer's personal GitHub
// OAuth connection. The deploy token is rotated as part of the round-
// trip — the previous token stays valid through the model's grace
// window so in-flight CI doesn't snap. Response carries the canonical
// secret name list and the new token's last-4 (the only piece of the
// new plaintext that ever crosses back to the browser).

export const PUSH_CI_SECRETS_TO_REPO = gql`
  mutation PushAstroliftCiSecretsToRepo($input: PushCiSecretsToRepoInput!) {
    pushAstroliftCiSecretsToRepo(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        secretNames
        rotatedTokenLast4
        repo
      }
    }
  }
`;
