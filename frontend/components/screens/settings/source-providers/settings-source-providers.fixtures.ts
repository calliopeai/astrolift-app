import type {
  AstroliftFactoryGateApp,
  AstroliftSourceConnection,
  AstroliftSshDeployKey,
  AstroliftWebhookSecretReveal,
} from "@/graphql/scm/scm.types";

import type { FactoryGateAppData, RotateFactoryGateKeyData } from "./use-factory-gate-app";
import type {
  AddClientIdData,
  DeployKeysData,
  GenerateSshKeyData,
  SourceHostsData,
} from "./use-source-providers";

/**
 * Hand-typed fixtures for the settings source-providers group: the
 * Source providers screen, the Add Client ID sheet, the Generate SSH
 * key sheet and the factory gate App.
 */

const noop = () => {};
const resolved = async () => {};

export function connection(
  name: string,
  patch: Partial<AstroliftSourceConnection> = {}
): AstroliftSourceConnection {
  return {
    id: `sc-${name}`,
    name,
    displayName: name,
    kind: "github_app_install",
    apiBaseUrl: "https://api.github.com",
    accountLogin: "acme-corp",
    appClientId: "Iv23lic8662KXwe4XKEI",
    oauthClientId: "1048576",
    oauthRedirectUri: "",
    installationId: "53811942",
    isActive: true,
    isOauthAppConfig: false,
    isPersonal: false,
    needsClientId: false,
    parentOauthAppId: null,
    repoVisibilityScopes: ["private_org", "public_org"],
    userUsername: null,
    lastUsedAt: "2026-09-27T14:05:00Z",
    tokenExpiresAt: null,
    createdAt: "2026-09-01T12:00:00Z",
    updatedAt: "2026-09-27T14:05:00Z",
    status: "ACTIVE",
    statusDetail: "",
    ...patch,
  };
}

export const CONNECTIONS: AstroliftSourceConnection[] = [
  connection("acme-github-app"),
  connection("acme-github-app-legacy", {
    needsClientId: true,
    appClientId: "",
    accountLogin: "acme-legacy",
  }),
  connection("github-oauth", {
    kind: "github_oauth_app",
    isOauthAppConfig: true,
    accountLogin: "",
    repoVisibilityScopes: [],
  }),
  connection("leo-github", {
    kind: "github_oauth_user",
    isPersonal: true,
    userUsername: "leo",
    accountLogin: "leo",
    repoVisibilityScopes: ["user_repos"],
  }),
  connection("gitlab-group", {
    kind: "gitlab_oauth_app",
    isOauthAppConfig: true,
    apiBaseUrl: "https://gitlab.example.com/api/v4",
    accountLogin: "platform",
  }),
  connection("gitea-pat", {
    kind: "gitea_pat",
    apiBaseUrl: "https://git.internal.example.com/api/v1",
    accountLogin: "ci-bot",
    isActive: false,
    repoVisibilityScopes: ["public_non_org"],
    status: "INACTIVE",
    statusDetail: "Inactive in Astrolift.",
  }),
];

/**
 * One GitHub App row per status the server reports (#2601). ConflictHQ is the
 * reported case: the App is installed on calliopeai only, so that row is
 * active in Astrolift and has no installation on GitHub.
 */
export const STATUS_CONNECTIONS: AstroliftSourceConnection[] = [
  connection("GitHub App: astrolift-ai", { accountLogin: "calliopeai", repoVisibilityScopes: [] }),
  connection("GitHub App: astrolift-ai (ConflictHQ)", {
    accountLogin: "ConflictHQ",
    installationId: "",
    repoVisibilityScopes: [],
    status: "INSTALLATION_MISSING",
    statusDetail:
      "GitHub has no installation of this App on ConflictHQ. Install it under Providers → Source → Connect to GitHub → Install Astrolift on a GitHub org, with ConflictHQ as the org.",
  }),
  connection("GitHub App: astrolift-ai (acme-legacy)", {
    accountLogin: "acme-legacy",
    status: "INACTIVE",
    statusDetail:
      "GitHub refused this App's credentials (HTTP 401). Check that the App exists and is not suspended, and that its private key is current.",
  }),
  connection("GitHub App: astrolift-ai (acme-labs)", {
    accountLogin: "acme-labs",
    status: "UNVERIFIED",
    statusDetail: "GitHub answered HTTP 502 to the installation check.",
  }),
];

export function deployKey(
  name: string,
  patch: Partial<AstroliftSshDeployKey> = {}
): AstroliftSshDeployKey {
  return {
    id: `dk-${name}`,
    name,
    fingerprintSha256: "SHA256:k3Yf1q8m0Zb4tq9vJ2x6hL7cN5wR1pD8sE0aU3iO2yT",
    publicKey:
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJq3r8Vd0mYkP2x7sL9wQ4nB6tH1cF5uE8aZ0gR3iK2o astrolift",
    registeredAppSlug: null,
    isActive: true,
    lastUsedAt: null,
    createdAt: "2026-09-10T08:00:00Z",
    ...patch,
  };
}

export const KEYS: AstroliftSshDeployKey[] = [
  deployKey("org-deploy-key"),
  deployKey("storefront-deploy", { registeredAppSlug: "storefront" }),
];

export const REVEAL: AstroliftWebhookSecretReveal = {
  connectionId: "sc-acme-github-app",
  plaintextSecret: "whsec_7f3c2a91e11b0c4d02e5f79a1d33b8c6",
  webhookUrlPath: "/app/scm/webhooks/github/sc-acme-github-app",
};

/** The Hosts section's props less the list controller, which the story builds. */
export const HOSTS: Omit<SourceHostsData, "list"> = {
  rows: CONNECTIONS,
  totalCount: CONNECTIONS.length,
  nextCursor: null,
  loading: false,
  error: null,
  onRetry: noop,
  incompleteClientIdConnections: CONNECTIONS.filter((c) => c.needsClientId),
  disconnecting: false,
  rotatingSecret: false,
  disconnect: resolved,
  rotateSecret: async () => REVEAL,
  refreshConnections: noop,
};

/** The SSH deploy keys section's props less the list controller. */
export const DEPLOY_KEYS: Omit<DeployKeysData, "list"> = {
  rows: KEYS,
  totalCount: KEYS.length,
  nextCursor: null,
  loading: false,
  error: null,
  onRetry: noop,
  deletingKey: false,
  deleteKey: resolved,
  refreshKeys: noop,
};

export const LONG_CONNECTIONS: AstroliftSourceConnection[] = [
  connection("acme-corporation-platform-engineering-github-app-installation-production", {
    apiBaseUrl:
      "https://github.enterprise.acme-corporation-internal.example.com/api/v3/with/a/long/path",
    accountLogin: "acme-corporation-platform-engineering-organization",
    needsClientId: true,
    appClientId: "",
    repoVisibilityScopes: ["private_org", "public_org", "user_repos", "public_non_org"],
  }),
  connection("personal-token", {
    kind: "gitlab_oauth_user",
    isPersonal: true,
    userUsername: "a-very-long-gitlab-username-for-an-operator-account",
  }),
];

export const LONG_KEYS: AstroliftSshDeployKey[] = [
  deployKey("storefront-production-deploy-key-with-write-access-for-tag-releases", {
    registeredAppSlug: "storefront-production-customer-facing-web-application",
    fingerprintSha256: "SHA256:k3Yf1q8m0Zb4tq9vJ2x6hL7cN5wR1pD8sE0aU3iO2yTk3Yf1q8m0Zb4tq9vJ2x6h",
  }),
];

export const ADD_CLIENT_ID: AddClientIdData = {
  saving: false,
  save: async () => true,
};

export const GENERATE_KEY: GenerateSshKeyData = {
  generating: false,
  generate: async () => KEYS[0],
};

export function factoryGateApp(
  patch: Partial<AstroliftFactoryGateApp> = {}
): AstroliftFactoryGateApp {
  return {
    state: "INSTALLED",
    appId: "2214897",
    installationId: "84120533",
    ownerLogin: "acme-corp",
    permissions: [{ name: "members", access: "read" }],
    ...patch,
  };
}

export const NO_FACTORY_GATE_APP = factoryGateApp({
  state: "NOT_REGISTERED",
  appId: null,
  installationId: null,
  ownerLogin: null,
  permissions: [],
});

export const FACTORY_GATE: FactoryGateAppData = {
  app: factoryGateApp(),
  loading: false,
  error: null,
  onRetry: noop,
  onRegister: noop,
};

export const ROTATE_FACTORY_GATE_KEY: RotateFactoryGateKeyData = {
  rotating: false,
  rotate: async () => true,
};
