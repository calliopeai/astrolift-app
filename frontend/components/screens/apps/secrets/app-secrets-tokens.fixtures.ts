import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";

import type { DeployTokensScreenProps } from "./DeployTokensScreen";
import type { SecretHistoryPanelViewProps } from "./SecretHistoryPanel";
import type { SecretsScreenProps } from "./SecretsScreen";
import type {
  AppSecret,
  AppSecretBundleAttachment,
  DeployToken,
  DeployTokenSecretReveal,
  SecretHistoryEntry,
} from "./secrets.types";
import { ALL_ENVS } from "./use-app-secrets";

/** Hand-typed fixtures for the app Secrets and Deploy tokens tabs. */

const noop = () => {};
const asyncNoop = async () => {};
const yes = async () => true;

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

const DAY = 24 * 60 * 60 * 1000;
const inDays = (d: number) => new Date(Date.now() + d * DAY).toISOString();

function env(id: string, name: string): AstroliftAppEnvironment {
  return {
    id,
    name,
    registeredAppSlug: "storefront",
    clusterId: null,
    clusterSlug: "prod-us-east",
    clusterProviderPluginSlug: null,
    createdAt: "2026-08-01T12:00:00Z",
    deploysPaused: false,
    domainZone: null,
    ingressPaused: false,
    requiredApprovals: 0,
    settings: [],
    url: `https://${name}.storefront.example.com`,
  };
}

export const ENVIRONMENTS: AstroliftAppEnvironment[] = [
  env("env-1", "production"),
  env("env-2", "staging"),
];

const EDITOR = { id: "u-1", username: "leo", displayName: "Leo Mata" };

export const SECRETS: AppSecret[] = [
  {
    id: "s-1",
    key: "DATABASE_URL",
    environmentName: "production",
    source: "literal",
    bundleSlug: "",
    managedServiceKind: "",
    isMasked: true,
    lastEditedAt: "2026-09-26T09:14:00Z",
    lastEditedBy: EDITOR,
    expiresAt: inDays(5),
    setVia: "cli",
    scope: "production",
    deploysAsShown: true,
  },
  {
    id: "s-2",
    key: "STRIPE_SECRET_KEY",
    environmentName: "production",
    source: "literal",
    bundleSlug: "",
    managedServiceKind: "",
    isMasked: true,
    lastEditedAt: "2026-09-20T16:40:00Z",
    lastEditedBy: null,
    expiresAt: inDays(12),
    setVia: "env_paste",
    scope: "all",
    deploysAsShown: false,
  },
  {
    id: "s-3",
    key: "FEATURE_FLAGS_TOKEN",
    environmentName: "staging",
    source: "literal",
    bundleSlug: "",
    managedServiceKind: "",
    isMasked: true,
    lastEditedAt: "2026-09-01T08:00:00Z",
    lastEditedBy: EDITOR,
    expiresAt: inDays(-3),
    setVia: "web",
    scope: "preview:feature/login-redesign",
  },
  {
    id: "s-4",
    key: "SENTRY_DSN",
    environmentName: "production",
    source: "bundle",
    bundleSlug: "observability",
    managedServiceKind: "",
    isMasked: true,
    lastEditedAt: "2026-08-30T10:00:00Z",
    lastEditedBy: null,
    setVia: "bundle",
  },
  {
    id: "s-5",
    key: "REDIS_URL",
    environmentName: "production",
    source: "managed_service",
    bundleSlug: "",
    managedServiceKind: "redis",
    isMasked: true,
    lastEditedAt: null,
    lastEditedBy: null,
    expiresAt: inDays(60),
    setVia: "managed_service",
  },
];

export const ATTACHMENTS: AppSecretBundleAttachment[] = [
  {
    id: "att-1",
    registeredAppSlug: "storefront",
    environmentName: "production",
    bundleSlug: "observability",
    bundleName: "Observability",
    prefix: "",
    teamSlug: "platform",
    keyCount: 3,
    mergeOrder: 1,
    attachedAt: "2026-08-30T10:00:00Z",
  },
  {
    id: "att-2",
    registeredAppSlug: "storefront",
    environmentName: "staging",
    bundleSlug: "payments-sandbox",
    bundleName: "Payments sandbox",
    prefix: "PAY_",
    teamSlug: null,
    keyCount: 0,
    mergeOrder: 2,
    attachedAt: null,
  },
];

function proposal(i: number, summary?: string): AstroliftSecretChangeProposal {
  return {
    id: `prop-${i}`,
    registeredAppSlug: "storefront",
    environmentName: i % 2 ? "production" : "",
    op: "set",
    status: "pending",
    proposerUserId: "u-1",
    proposerDisplayName: "Leo Mata",
    requiredApproverCount: 2,
    approvalsCount: i % 2,
    expiresAt: "2026-10-05T00:00:00Z",
    decidedAt: null,
    appliedAt: null,
    applyError: "",
    createdAt: "2026-09-27T12:00:00Z",
    payload: {},
    payloadDiff: summary ? { summary } : {},
    approvals: [],
  };
}

export const PROPOSALS: AstroliftSecretChangeProposal[] = [
  proposal(1, "Set STRIPE_SECRET_KEY on production"),
  proposal(2),
];

/**
 * The screen's props less its slots and its list state: the stories answer
 * `keysList` / `keyRows` and `bundlesList` / `bundleRows` in memory.
 */
export const SECRETS_SCREEN: Omit<
  SecretsScreenProps,
  | "tabs"
  | "pushToGitHub"
  | "renderHistory"
  | "sectionHref"
  | "keysList"
  | "keyRows"
  | "keyTotal"
  | "bundlesList"
  | "bundleRows"
  | "bundleTotal"
> = {
  slug: "storefront",
  section: "keys",
  envName: ALL_ENVS,
  setEnvName: noop,
  secretsError: null,
  retrySecrets: noop,
  environments: ENVIRONMENTS,
  secrets: SECRETS,
  secretsLoading: false,
  attachments: ATTACHMENTS,
  attachmentsLoading: false,
  attachmentsError: null,
  retryAttachments: noop,
  pendingProposals: PROPOSALS,
  revealedValues: {},
  editingId: null,
  rotatingId: null,
  revealingId: null,
  busy: false,
  rotating: false,
  detaching: false,
  onToggleReveal: asyncNoop,
  onStartEdit: noop,
  onStartRotate: noop,
  onCancelEdit: noop,
  onInlineSave: yes,
  onRequestDelete: () => true,
  onDelete: asyncNoop,
  onDetach: asyncNoop,
  onSetSecret: yes,
  onBulkImport: yes,
};

export const SECRETS_EMPTY: typeof SECRETS_SCREEN = {
  ...SECRETS_SCREEN,
  secrets: [],
  attachments: [],
  pendingProposals: [],
};

export const SECRETS_LOADING: typeof SECRETS_SCREEN = {
  ...SECRETS_EMPTY,
  secretsLoading: true,
  attachmentsLoading: true,
};

export const SECRETS_LONG: typeof SECRETS_SCREEN = {
  ...SECRETS_SCREEN,
  slug: LONG,
  environments: [env("env-long", LONG)],
  secrets: [
    {
      ...SECRETS[0],
      key: `DATABASE_URL_${LONG.toUpperCase().replace(/-/g, "_")}`,
      environmentName: LONG,
      scope: `preview:feature/${LONG}`,
    },
    { ...SECRETS[3], bundleSlug: LONG },
  ],
  revealedValues: { "s-1": `postgres://app:${LONG}@db.${LONG}.internal:5432/storefront` },
  attachments: [
    { ...ATTACHMENTS[0], bundleName: LONG, bundleSlug: LONG, teamSlug: LONG, prefix: `${LONG}_` },
  ],
  pendingProposals: Array.from({ length: 7 }, (_, i) =>
    proposal(i + 1, `Set ${LONG.toUpperCase()} on ${LONG}`)
  ),
};

export const HISTORY_ENTRIES: SecretHistoryEntry[] = [
  {
    timestamp: "2026-09-26T09:14:00Z",
    action: "app.secret.rotate",
    success: true,
    errorCode: null,
    sourceIp: "203.0.113.7",
    actor: { id: "u-1", username: "leo" },
  },
  {
    timestamp: "2026-09-25T18:02:00Z",
    action: "app.secret.reveal",
    success: false,
    errorCode: "PERMISSION_DENIED",
    sourceIp: null,
    actor: { id: "u-2", username: "keith" },
  },
  {
    timestamp: "2026-09-01T08:00:00Z",
    action: "app.secret.set",
    success: true,
    errorCode: null,
    sourceIp: null,
    actor: null,
  },
];

export const HISTORY: SecretHistoryPanelViewProps = {
  secretKey: "DATABASE_URL",
  entries: HISTORY_ENTRIES,
  loading: false,
  error: false,
};

export const TOKENS: DeployToken[] = [
  {
    id: "t-1",
    name: "github-actions",
    last4: "9f2c",
    scopes: ["deploy", "rollback"],
    expiresAt: "2027-09-28T00:00:00Z",
    lastUsedAt: "2026-09-27T21:10:00Z",
    lastUsedIp: "140.82.112.4",
    lastUsedAgent: "astro-cli/3.3.1 (linux; github-actions)",
    isRevoked: false,
    lastRotatedAt: "2026-06-01T00:00:00Z",
    registeredAppSlug: "storefront",
    createdAt: "2026-01-10T00:00:00Z",
  },
  {
    id: "t-2",
    name: "buildkite",
    last4: "a01b",
    scopes: [],
    expiresAt: null,
    lastUsedAt: "2026-09-12T11:00:00Z",
    lastUsedIp: "",
    lastUsedAgent: "",
    isRevoked: false,
    lastRotatedAt: null,
    registeredAppSlug: "storefront",
    createdAt: "2026-03-02T00:00:00Z",
  },
  {
    id: "t-3",
    name: "old-jenkins",
    last4: "77de",
    scopes: ["deploy"],
    expiresAt: "2026-05-01T00:00:00Z",
    lastUsedAt: null,
    lastUsedIp: "",
    lastUsedAgent: "",
    isRevoked: true,
    lastRotatedAt: null,
    registeredAppSlug: "storefront",
    createdAt: "2025-05-01T00:00:00Z",
  },
];

export const TOKEN_REVEAL: DeployTokenSecretReveal = {
  token: TOKENS[0],
  plaintextSecret: "astro_dt_4f9c1e2b7a6d4c3f8e1b0a9d7c6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9f2c",
  rotationGraceSeconds: 86400,
};

/** The screen's props less its tab bar and its list state (the stories keep that in memory). */
export const TOKENS_SCREEN: Omit<DeployTokensScreenProps, "tabs" | "list"> = {
  slug: "storefront",
  rows: TOKENS,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  nextCursor: null,
  totalCount: TOKENS.length,
  busy: false,
  reveal: null,
  onDismissReveal: noop,
  onCreate: yes,
  onRotate: asyncNoop,
  onRevoke: asyncNoop,
};

export const TOKENS_LONG: typeof TOKENS_SCREEN = {
  ...TOKENS_SCREEN,
  slug: LONG,
  rows: [
    {
      ...TOKENS[0],
      name: LONG,
      scopes: ["deploy", "rollback", LONG],
      lastUsedAgent: `Mozilla/5.0 ${LONG} ${LONG}`,
    },
  ],
  totalCount: 1,
};
