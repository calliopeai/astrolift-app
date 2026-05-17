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
  commitSha
  commitMessage
  commitAuthor
  branch
  ciActorKind
  ciRunUrl
  ciProvider
  repoUrl
  abortedReason
  triggeredByUserId
  triggeredByMe
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
  mutation AbortDeployment($input: AbortDeploymentInput!) {
    abortDeployment(input: $input) {
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

export const REJECT_DEPLOYMENT = gql`
  mutation RejectDeployment($input: AbortDeploymentInput!) {
    rejectDeployment(input: $input) {
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

// --- Force redeploy recovery (#389) -------------------------------
//
// Recovery action for wedged apps. The mutation cancels any in-flight
// Deployment rows, deletes the per-workload k8s objects (Deployment /
// Service / Ingress / CronJob plus bare-slug fallbacks), and re-fires
// the CI deploy workflow. ``confirmSlug`` must equal the app's slug —
// the muscle-memory guard the Settings modal binds the typed-slug
// field to. Dispatch failures don't roll back the cancellation +
// delete steps; the payload's ``workflowDispatched`` flag + counts
// carry the partial-success shape into the success toast.

export const FORCE_REDEPLOY = gql`
  mutation ForceAstroliftRedeploy($input: ForceRedeployInput!) {
    forceAstroliftRedeploy(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        deploymentsCancelled
        k8sObjectsDeleted
        workflowDispatched
        runUrl
        dispatchMessage
      }
    }
  }
`;

// --- Danger-zone hard deregister (#392) ----------------------------
//
// Tears down every per-app cloud resource and soft-deletes the
// platform rows once teardown converges. The mutation kicks off the
// ``DeregisterAppWorkflow`` and returns the deterministic workflow
// id immediately — the FE redirects to the org's app list and the
// workflow does the per-resource work async. Re-firing the mutation
// (same ``app_slug``) joins the existing run via Temporal de-dup so
// a partial failure is a one-click retry from the same surface.

export const DEREGISTER_APP = gql`
  mutation DeregisterAstroliftApp($input: DeregisterAppInput!) {
    deregisterAstroliftApp(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        workflowId
        stillLiveResources
      }
    }
  }
`;

// --- Run scheduled job once (#390) --------------------------------
//
// Spawn a one-shot k8s Job from a manifest-declared CronJob without
// touching the schedule. The backend re-renders the cronjob's
// jobTemplate as a standalone Job with a fresh name
// ``<job_slug>-manual-<8hex>`` and applies it through the cluster
// driver. Recorded as a ``ScheduledJobRun`` with
// ``triggerKind="manual"`` so the existing jobs surface picks it up
// alongside controller-spawned runs. ``logsUrl`` points at the
// scheduled-job-runs page on success — the toast links there so the
// operator can watch the run materialize.

export const RUN_JOB_ONCE = gql`
  mutation RunAstroliftJobOnce($input: RunJobOnceInput!) {
    runAstroliftJobOnce(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        runName
        namespace
        logsUrl
      }
    }
  }
`;
