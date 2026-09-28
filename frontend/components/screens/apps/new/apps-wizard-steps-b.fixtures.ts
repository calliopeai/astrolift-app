import type { AstroliftRemoteRepo, AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import type { RepoPickerStepViewProps } from "./RepoPickerStep";
import type { ReviewSubmitStepViewProps } from "./ReviewSubmitStep";

/** Hand-typed fixtures for wizard steps 1 (repo picker) and 5 (review + submit). */

const noop = () => {};

export const LONG_NAME =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

// ----- Step 1: repo picker ------------------------------------------------

function connection(
  overrides: Partial<AstroliftSourceConnection> & Pick<AstroliftSourceConnection, "id" | "kind">
): AstroliftSourceConnection {
  return {
    accountLogin: "conflict-hq",
    apiBaseUrl: "https://api.github.com",
    appClientId: "",
    createdAt: "2026-08-01T12:00:00Z",
    displayName: "",
    installationId: "",
    isActive: true,
    isOauthAppConfig: false,
    isPersonal: false,
    lastUsedAt: "2026-09-27T09:00:00Z",
    name: "github",
    needsClientId: false,
    oauthClientId: "",
    oauthRedirectUri: "",
    parentOauthAppId: null,
    repoVisibilityScopes: ["private_org", "public_org"],
    tokenExpiresAt: null,
    updatedAt: "2026-09-27T09:00:00Z",
    userUsername: null,
    ...overrides,
  };
}

function repo(fullName: string, overrides: Partial<AstroliftRemoteRepo> = {}): AstroliftRemoteRepo {
  const name = fullName.split("/").pop() ?? fullName;
  return {
    cloneUrlHttps: `https://github.com/${fullName}.git`,
    cloneUrlSsh: `git@github.com:${fullName}.git`,
    defaultBranch: "main",
    description: "",
    fullName,
    isArchived: false,
    isFork: false,
    name,
    pushedAt: "2026-09-20T12:00:00Z",
    visibility: "private",
    webUrl: `https://github.com/${fullName}`,
    ...overrides,
  };
}

export const CONNECTIONS: AstroliftSourceConnection[] = [
  connection({
    id: "c0ffee00-0000-4000-8000-0000000000a1",
    kind: "github_app_install",
    displayName: "CONFLICT GitHub App",
    name: "github-app",
  }),
  connection({
    id: "c0ffee00-0000-4000-8000-0000000000a2",
    kind: "gitlab_oauth_user",
    displayName: "",
    name: "gitlab.com (leo)",
    apiBaseUrl: "https://gitlab.com/api/v4",
  }),
];

export const REPOS: AstroliftRemoteRepo[] = [
  repo("conflict-hq/api-gateway"),
  repo("conflict-hq/web-frontend", { visibility: "public", defaultBranch: "trunk" }),
  repo("conflict-hq/legacy-billing", { isArchived: true, pushedAt: "2024-02-01T12:00:00Z" }),
  repo("leo/api-gateway", { isFork: true, visibility: "public" }),
];

const SCM: RepoPickerStepViewProps["scm"] = {
  accounts: [
    {
      providerConfigId: "c0ffee00-0000-4000-8000-0000000000b1",
      providerKind: "github_oauth_app",
      providerLabel: "GitHub",
      isConnected: false,
      reauthRequired: false,
      expiresAt: null,
      lastUsedAt: null,
      linkedAccountLogin: null,
    },
  ],
  loading: false,
  starting: false,
  startConnect: async () => {},
};

/** A connection is picked and a repo chosen: the full step. */
export const REPO_PICKER: RepoPickerStepViewProps = {
  clusterLoading: false,
  noCluster: false,
  connectionsLoading: false,
  connections: CONNECTIONS,
  connectionId: CONNECTIONS[0].id,
  sourceRepo: "conflict-hq/api-gateway",
  defaultBranch: "main",
  onConnectionChange: noop,
  reposLoading: false,
  repoList: { repos: REPOS, recoverable: false, errorCode: null, errorMessage: null },
  search: "",
  onSearchChange: noop,
  onPickRepo: noop,
  repoToAppCount: new Map([["conflict-hq/api-gateway", 2]]),
  scm: SCM,
};

export const REPO_PICKER_LOADING: RepoPickerStepViewProps = {
  ...REPO_PICKER,
  clusterLoading: true,
  connections: [],
  connectionId: "",
  sourceRepo: "",
  repoList: undefined,
};

export const REPO_PICKER_NO_CLUSTER: RepoPickerStepViewProps = {
  ...REPO_PICKER_LOADING,
  clusterLoading: false,
  noCluster: true,
};

export const REPO_PICKER_NO_CONNECTIONS: RepoPickerStepViewProps = {
  ...REPO_PICKER_LOADING,
  clusterLoading: false,
};

/** Connections loaded, none picked yet (more than one, so no auto-pick). */
export const REPO_PICKER_UNPICKED: RepoPickerStepViewProps = {
  ...REPO_PICKER,
  connectionId: "",
  sourceRepo: "",
  defaultBranch: "",
  repoList: undefined,
};

export const REPO_PICKER_REPOS_LOADING: RepoPickerStepViewProps = {
  ...REPO_PICKER,
  sourceRepo: "",
  reposLoading: true,
  repoList: undefined,
};

export const REPO_PICKER_NO_REPOS: RepoPickerStepViewProps = {
  ...REPO_PICKER,
  sourceRepo: "",
  repoList: { repos: [], recoverable: false, errorCode: null, errorMessage: null },
};

/** The host refused the token; recoverable, so the reauth action shows. */
export const REPO_PICKER_REPOS_ERROR: RepoPickerStepViewProps = {
  ...REPO_PICKER,
  connections: [CONNECTIONS[1]],
  connectionId: CONNECTIONS[1].id,
  sourceRepo: "",
  repoList: {
    repos: [],
    recoverable: true,
    errorCode: "token_expired",
    errorMessage: "The GitLab token for this connection expired. Reconnect to list repositories.",
  },
};

export const REPO_PICKER_LONG: RepoPickerStepViewProps = {
  ...REPO_PICKER,
  connections: [
    connection({
      id: "c0ffee00-0000-4000-8000-0000000000a3",
      kind: "github_pat",
      displayName: `Personal access token for ${LONG_NAME}`,
    }),
  ],
  connectionId: "c0ffee00-0000-4000-8000-0000000000a3",
  sourceRepo: `conflict-hq/${LONG_NAME}`,
  defaultBranch: `release/${LONG_NAME}`,
  repoList: {
    repos: [repo(`conflict-hq/${LONG_NAME}`, { defaultBranch: `release/${LONG_NAME}` })],
    recoverable: false,
    errorCode: null,
    errorMessage: null,
  },
};

// ----- Step 5: review + submit --------------------------------------------

export const STEPS = [
  { label: "Repository" },
  { label: "Manifest" },
  { label: "App details" },
  { label: "Deploy strategy" },
  { label: "Review" },
];

const REVIEW_STATE: ReviewSubmitStepViewProps["state"] = {
  sourceRepo: "conflict-hq/api-gateway",
  defaultBranch: "main",
  sourceKind: "github",
  manifestPath: "astrolift.toml",
  manifestFromRepo: true,
  manifestValid: true,
  name: "Api Gateway",
  slug: "api-gateway",
  description: "Edge API for the customer portal.",
  deployTiming: "now",
  triggerMode: "cron",
  deployBranch: "main",
  cronExpression: "0 3 * * 1-5",
  requiresApproval: true,
  approverUserIds: ["u1", "u2", "u3"],
  approverTeamId: "",
  minimumApprovals: 2,
  connectionIsAppInstall: true,
  pushCiWorkflow: true,
  triggerFirstDeploy: true,
};

/** Before submit: every side effect pending. */
export const REVIEW: ReviewSubmitStepViewProps = {
  state: REVIEW_STATE,
  steps: STEPS,
  onJumpToStep: noop,
  canPushCiWorkflow: true,
  onPushCiWorkflowChange: noop,
  onTriggerFirstDeployChange: noop,
  submitting: false,
  submitError: null,
  sideEffects: [],
};

/** Submit clicked, the plan not built yet. */
export const REVIEW_SUBMITTING: ReviewSubmitStepViewProps = {
  ...REVIEW,
  submitting: true,
};

/** Mid-run: register done, CI push running, first deploy pending. */
export const REVIEW_RUNNING: ReviewSubmitStepViewProps = {
  ...REVIEW,
  submitting: true,
  sideEffects: [
    { key: "register", label: "Register", status: "done" },
    { key: "ci_workflow", label: "CI workflow", status: "running" },
    { key: "first_deploy", label: "First deploy", status: "pending" },
  ],
};

export const REVIEW_FAILED: ReviewSubmitStepViewProps = {
  ...REVIEW,
  submitError: "pushCiWorkflow: the connection's token cannot write to conflict-hq/api-gateway.",
  sideEffects: [
    { key: "register", label: "Register", status: "done" },
    {
      key: "ci_workflow",
      label: "CI workflow",
      status: "failed",
      error: "403 Resource not accessible by integration",
    },
    { key: "first_deploy", label: "First deploy", status: "skipped" },
  ],
};

/** Minimal inputs: skip deploy config, PAT-less connection, empty fields show a dash. */
export const REVIEW_MINIMAL: ReviewSubmitStepViewProps = {
  ...REVIEW,
  canPushCiWorkflow: false,
  state: {
    ...REVIEW_STATE,
    sourceKind: "gitlab",
    defaultBranch: "",
    manifestFromRepo: false,
    manifestValid: false,
    description: "",
    deployTiming: "skip",
    connectionIsAppInstall: false,
    pushCiWorkflow: false,
    triggerFirstDeploy: false,
  },
};

export const REVIEW_LONG: ReviewSubmitStepViewProps = {
  ...REVIEW,
  state: {
    ...REVIEW_STATE,
    sourceRepo: `conflict-hq/${LONG_NAME}`,
    defaultBranch: `release/${LONG_NAME}`,
    manifestPath: `services/${LONG_NAME}/astrolift.toml`,
    name: LONG_NAME,
    slug: LONG_NAME.slice(0, 40),
    description: `An app whose description goes on and on: ${LONG_NAME} ${LONG_NAME}`,
    deployBranch: `release/${LONG_NAME}`,
    approverUserIds: [],
    approverTeamId: "team-1",
  },
  submitError: `registerApp failed: ${LONG_NAME} ${LONG_NAME} ${LONG_NAME}`,
};
