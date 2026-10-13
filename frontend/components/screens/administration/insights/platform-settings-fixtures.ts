/** Hand-typed fixtures for PlatformSettingsScreen (#1861). */
import type { PlatformSettingsScreenProps } from "./PlatformSettingsScreen";
import type { PlatformSetting } from "./use-platform-settings";

const row = (setting: Partial<PlatformSetting> & Pick<PlatformSetting, "key" | "group">) =>
  ({
    __typename: "AstroliftPlatformSetting",
    kind: "int",
    helpText: "",
    value: "",
    default: "",
    changed: false,
    choices: [],
    editable: true,
    readOnlyReason: "",
    warning: "",
    ...setting,
  }) as PlatformSetting;

export const PLATFORM_SETTINGS_ROWS: PlatformSetting[] = [
  row({
    key: "AGENT_POD_UID",
    group: "Agent pods",
    helpText: "uid non-root agent pods run as. Must be the agent user baked into the agent images.",
    value: "42042",
    default: "42042",
    warning:
      "Agent images must own this uid. A uid they do not own breaks every non-root agent pod.",
  }),
  row({
    key: "AGENT_EGRESS_ALLOW_CIDRS",
    group: "Agent pods",
    kind: "string",
    helpText: "Comma-separated CIDRs agent pods may reach past the network fence.",
    value: "10.0.0.0/8",
    default: "",
    changed: true,
  }),
  row({
    key: "VLLM_FRONTEND_DEFAULT",
    group: "Model hosting",
    kind: "choice",
    helpText: "Install-wide vLLM API frontend for self-hosted models: rust or python.",
    value: "rust",
    default: "rust",
    choices: ["rust", "python"],
  }),
  row({
    key: "TEMPORAL_ENABLED",
    group: "Workflows",
    kind: "bool",
    helpText: "Durable workflow runtime.",
    value: "true",
    default: "true",
    editable: false,
    readOnlyReason: "A feature flag: switch it under Administration > Features.",
  }),
  row({
    key: "REQUIRE_STEP_UP_AUTH",
    group: "Step-up auth",
    kind: "bool",
    helpText: "Sensitive actions need a fresh sign-in.",
    value: "false",
    default: "false",
    warning: "Turning this off lets sensitive actions run without a fresh sign-in.",
  }),
  row({
    key: "GOOGLE_PLAY_INTEGRITY_API_KEY",
    group: "Device attestation",
    kind: "string",
    helpText: "Play Integrity API key.",
    editable: false,
    readOnlyReason: "A credential: set it in the Django admin, never sent to a browser.",
  }),
];

export const PLATFORM_SETTINGS: PlatformSettingsScreenProps = {
  settings: PLATFORM_SETTINGS_ROWS,
  loading: false,
  error: null,
  onRetry: () => {},
  pendingKey: null,
  save: async () => {},
  reset: async () => {},
};

export const PLATFORM_SECRETS: NonNullable<PlatformSettingsScreenProps["secrets"]> = {
  stores: [
    {
      organizationSlug: "conflict",
      organizationName: "CONFLICT",
      clusterSlug: "conflict-astrolift",
      sharedCluster: false,
      specCount: 3,
    },
    {
      organizationSlug: "empty-org",
      organizationName: "Empty Org",
      clusterSlug: "",
      sharedCluster: false,
      specCount: 0,
    },
  ],
  storesLoading: false,
  storesError: null,
  inventory: {
    provider: "aws-secrets-manager",
    canReveal: true,
    store: { organizationSlug: "conflict" },
    refs: [
      {
        specSlug: "claude-dev",
        envVar: "GITHUB_TOKEN",
        uri: "sm:agents/01a05978/gh",
        exists: true,
        error: "",
      },
      {
        specSlug: "claude-dev",
        envVar: "SLACK_TOKEN",
        uri: "sm:agents/01a05978/slack",
        exists: false,
        error: "",
      },
      {
        specSlug: "reviewer",
        envVar: "OTHER_TEAM_TOKEN",
        uri: "sm:agents/01a05978/teams/other/token",
        exists: false,
        error: "outside this spec's owner namespace",
      },
    ],
  },
  inventoryLoading: false,
  inventoryError: null,
  inspect: () => {},
};
