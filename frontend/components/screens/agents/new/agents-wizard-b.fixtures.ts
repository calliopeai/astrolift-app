import type {
  AstroliftDiscoveredAgentManifest,
  AstroliftRegisteredAgent,
} from "@/graphql/agents/agents.types";
import type { AstroliftRemoteRepo, AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import type { AgentRepoPickerStepViewProps } from "./AgentRepoPickerStep";
import type { AgentReviewSubmitStepViewProps } from "./AgentReviewSubmitStep";

/** Hand-typed fixtures for New agent: the repo picker (Source) and Review. */

const noop = () => {};

export const LONG_NAME =
  "platform-team-shared-production-agents-us-west-2-with-a-deliberately-long-name-that-keeps-going";

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
  repo("conflict-hq/company-agents"),
  repo("conflict-hq/sales-agents", { visibility: "public", defaultBranch: "trunk" }),
  repo("conflict-hq/legacy-bots", { isArchived: true, pushedAt: "2024-02-01T12:00:00Z" }),
  repo("leo/company-agents", { isFork: true, visibility: "public" }),
];

const SCM: AgentRepoPickerStepViewProps["scm"] = {
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

/** A connection is picked and a repo chosen: the full step, ref input shown. */
export const AGENT_REPO_PICKER: AgentRepoPickerStepViewProps = {
  connectionsLoading: false,
  connections: CONNECTIONS,
  connectionId: CONNECTIONS[0].id,
  sourceRepo: "conflict-hq/company-agents",
  refValue: "main",
  onConnectionChange: noop,
  reposLoading: false,
  repoList: { repos: REPOS, recoverable: false, errorCode: null, errorMessage: null },
  search: "",
  onSearchChange: noop,
  onPickRepo: noop,
  onRefChange: noop,
  repoToAgentCount: new Map([
    ["conflict-hq/company-agents", 3],
    ["conflict-hq/sales-agents", 1],
  ]),
  scm: SCM,
};

export const AGENT_REPO_PICKER_LOADING: AgentRepoPickerStepViewProps = {
  ...AGENT_REPO_PICKER,
  connectionsLoading: true,
  connections: [],
  connectionId: "",
  sourceRepo: "",
  repoList: undefined,
};

export const AGENT_REPO_PICKER_NO_CONNECTIONS: AgentRepoPickerStepViewProps = {
  ...AGENT_REPO_PICKER_LOADING,
  connectionsLoading: false,
};

/** Connections loaded, none picked yet (more than one, so no auto-pick). */
export const AGENT_REPO_PICKER_UNPICKED: AgentRepoPickerStepViewProps = {
  ...AGENT_REPO_PICKER,
  connectionId: "",
  sourceRepo: "",
  repoList: undefined,
};

export const AGENT_REPO_PICKER_REPOS_LOADING: AgentRepoPickerStepViewProps = {
  ...AGENT_REPO_PICKER,
  sourceRepo: "",
  reposLoading: true,
  repoList: undefined,
};

export const AGENT_REPO_PICKER_NO_REPOS: AgentRepoPickerStepViewProps = {
  ...AGENT_REPO_PICKER,
  sourceRepo: "",
  repoList: { repos: [], recoverable: false, errorCode: null, errorMessage: null },
};

/** The host refused the token; recoverable, so the reauth action shows. */
export const AGENT_REPO_PICKER_REPOS_ERROR: AgentRepoPickerStepViewProps = {
  ...AGENT_REPO_PICKER,
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

export const AGENT_REPO_PICKER_LONG: AgentRepoPickerStepViewProps = {
  ...AGENT_REPO_PICKER,
  connections: [
    connection({
      id: "c0ffee00-0000-4000-8000-0000000000a3",
      kind: "github_pat",
      displayName: `Personal access token for ${LONG_NAME}`,
    }),
  ],
  connectionId: "c0ffee00-0000-4000-8000-0000000000a3",
  sourceRepo: `conflict-hq/${LONG_NAME}`,
  refValue: `release/${LONG_NAME}`,
  repoList: {
    repos: [repo(`conflict-hq/${LONG_NAME}`, { defaultBranch: `release/${LONG_NAME}` })],
    recoverable: false,
    errorCode: null,
    errorMessage: null,
  },
  repoToAgentCount: new Map([[`conflict-hq/${LONG_NAME}`, 12]]),
};

// ----- Review + submit ---------------------------------------------------

function manifest(
  slug: string,
  overrides: Partial<AstroliftDiscoveredAgentManifest> = {}
): AstroliftDiscoveredAgentManifest {
  return {
    manifestPath: `agents/${slug}/astrolift.toml`,
    name: slug
      .split("-")
      .map((w) => w[0].toUpperCase() + w.slice(1))
      .join(" "),
    slug,
    workloadKind: "agent",
    alreadyRegistered: false,
    ...overrides,
  };
}

export const DISCOVERED: AstroliftDiscoveredAgentManifest[] = [
  manifest("bdr-outreach"),
  manifest("demand-scout"),
  manifest("weekly-digest", { alreadyRegistered: true }),
];

export const AGENT_REVIEW: AgentReviewSubmitStepViewProps = {
  state: {
    sourceRepo: "conflict-hq/company-agents",
    ref: "main",
    defaultBranch: "main",
    sourceKind: "github",
    discoveredAgents: DISCOVERED,
    projectId: "c0ffee00-0000-4000-8000-0000000000p1",
  },
  projectLabel: "sales/outbound",
  onJumpToStep: noop,
  submitting: false,
  submitError: null,
  sideEffects: [],
  registeredAgents: [],
};

/** Submit in flight, before the side-effect plan exists: the "Submitting…" line. */
export const AGENT_REVIEW_SUBMITTING: AgentReviewSubmitStepViewProps = {
  ...AGENT_REVIEW,
  submitting: true,
};

/** Register step running. */
export const AGENT_REVIEW_RUNNING: AgentReviewSubmitStepViewProps = {
  ...AGENT_REVIEW,
  submitting: true,
  sideEffects: [{ key: "register", label: "Register agents", status: "running" }],
};

/** Nothing was discovered (the discovery step normally blocks this; closest real state). */
export const AGENT_REVIEW_EMPTY: AgentReviewSubmitStepViewProps = {
  ...AGENT_REVIEW,
  state: { ...AGENT_REVIEW.state, discoveredAgents: [] },
  projectLabel: "",
};

export const AGENT_REVIEW_FAILED: AgentReviewSubmitStepViewProps = {
  ...AGENT_REVIEW,
  submitError: "registerAgentRepo: the connection token lacks read access to this repository.",
  sideEffects: [
    {
      key: "register",
      label: "Register agents",
      status: "failed",
      error: "the connection token lacks read access to this repository.",
    },
  ],
};

const REGISTERED: AstroliftRegisteredAgent[] = [
  {
    manifestPath: "agents/bdr-outreach/astrolift.toml",
    slug: "bdr-outreach",
    appId: "c0ffee00-0000-4000-8000-0000000000d1",
    workloadSlug: "bdr-outreach",
    created: true,
  },
  {
    manifestPath: "agents/demand-scout/astrolift.toml",
    slug: "demand-scout",
    appId: "c0ffee00-0000-4000-8000-0000000000d2",
    workloadSlug: "demand-scout",
    created: true,
  },
  {
    manifestPath: "agents/weekly-digest/astrolift.toml",
    slug: "weekly-digest",
    appId: "c0ffee00-0000-4000-8000-0000000000d3",
    workloadSlug: "weekly-digest",
    created: false,
  },
];

/** Submit succeeded: per-agent outcomes shown, register step done. */
export const AGENT_REVIEW_DONE: AgentReviewSubmitStepViewProps = {
  ...AGENT_REVIEW,
  sideEffects: [{ key: "register", label: "Register agents", status: "done" }],
  registeredAgents: REGISTERED,
};

export const AGENT_REVIEW_LONG: AgentReviewSubmitStepViewProps = {
  ...AGENT_REVIEW,
  state: {
    ...AGENT_REVIEW.state,
    sourceRepo: `conflict-hq/${LONG_NAME}`,
    ref: `release/${LONG_NAME}`,
    discoveredAgents: [
      manifest(`agent-${LONG_NAME}`, {
        manifestPath: `agents/${LONG_NAME}/nested/${LONG_NAME}/astrolift.toml`,
      }),
      ...DISCOVERED,
    ],
  },
  projectLabel: `${LONG_NAME}/${LONG_NAME}`,
};
