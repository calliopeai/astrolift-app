import type { AstroliftApproverUser } from "@/graphql/identity/identity.types";

import type { AppDeployStrategyFields } from "./AppDeployStrategyStep";
import type { ApproverSelection } from "./ApproverSelector";
import type { ManifestPreviewFields } from "./ManifestPreviewStep";
import type { ApproverSelectorData } from "./use-approver-selector";

// ── Approver picker ────────────────────────────────────────────────

export const APPROVER_USERS: AstroliftApproverUser[] = [
  { id: "u-1", displayName: "Ada Lovelace", email: "ada@example.com", avatarUrl: "" },
  { id: "u-2", displayName: "Grace Hopper", email: "grace@example.com", avatarUrl: "" },
  { id: "u-3", displayName: "Linus Torvalds", email: "linus@example.com", avatarUrl: "" },
  { id: "u-4", displayName: "", email: "ops-bot@example.com", avatarUrl: "" },
];

export const APPROVER_DATA: ApproverSelectorData = {
  allUsers: APPROVER_USERS,
  orgTeams: [
    { id: "t-1", slug: "platform", name: "Platform" },
    { id: "t-2", slug: "sre", name: "Site Reliability" },
  ],
  usersLoading: false,
  usersError: null,
};

export const APPROVER_LOADING: ApproverSelectorData = {
  allUsers: [],
  orgTeams: [],
  usersLoading: true,
  usersError: null,
};

export const APPROVER_EMPTY: ApproverSelectorData = {
  allUsers: [],
  orgTeams: [],
  usersLoading: false,
  usersError: null,
};

export const APPROVER_ERROR: ApproverSelectorData = {
  allUsers: [],
  orgTeams: [],
  usersLoading: false,
  usersError: "Network error: failed to load organization members",
};

export const APPROVER_LONG: ApproverSelectorData = {
  allUsers: [
    {
      id: "u-long",
      displayName: "Maximiliana Theodora Wolfeschlegelsteinhausenbergerdorff-Alexandropoulou",
      email:
        "maximiliana.theodora.wolfeschlegelsteinhausenbergerdorff@very-long-subdomain.example.com",
      avatarUrl: "",
    },
    ...APPROVER_USERS,
  ],
  orgTeams: [
    {
      id: "t-long",
      slug: "platform-reliability-and-developer-experience-guild",
      name: "Platform Reliability and Developer Experience Guild (EMEA and APAC rotation)",
    },
  ],
  usersLoading: false,
  usersError: null,
};

export const NO_SELECTION: ApproverSelection = {
  approverUserIds: [],
  approverTeamId: "",
  minimumApprovals: 1,
};

/** Two picked users, plus one id no longer in the live picker (orphan). */
export const USERS_SELECTION: ApproverSelection = {
  approverUserIds: ["u-1", "u-2", "u-gone"],
  approverTeamId: "",
  minimumApprovals: 2,
};

export const TEAM_SELECTION: ApproverSelection = {
  approverUserIds: [],
  approverTeamId: "t-1",
  minimumApprovals: 1,
};

// ── Deploy strategy ────────────────────────────────────────────────

export const DEPLOY_STRATEGY_STATE: AppDeployStrategyFields = {
  deployTiming: "now",
  triggerMode: "auto_on_push",
  deployBranch: "main",
  defaultBranch: "main",
  cronExpression: "0 * * * *",
  requiresApproval: false,
  approverUserIds: [],
  approverTeamId: "",
  minimumApprovals: 1,
  triggerFirstDeploy: true,
};

// ── Manifest preview ───────────────────────────────────────────────

export const VALID_MANIFEST = `name = "storefront"

[[workloads]]
slug = "web"
kind = "deployment"
replicas = 2

[workloads.containers.app]
image = "ghcr.io/example/storefront"
port = 8080
`;

export const AGENT_CONFIG_MANIFEST = `astrolift_version = "1"

[skills.triage]
description = "Triage incoming tickets"

[tools.search]
kind = "http"
`;

export const MASKED_MANIFEST = `name = "storefront"

[[workloads]]
slug = "web"
kind = "deployment"

[workloads.containers.app]
image = "ghcr.io/example/storefront"
port = 8080

[env]
DATABASE_URL = "[ASTROLIFT_REDACTED_ENV_VALUE]"
`;

export const MANIFEST_STATE: ManifestPreviewFields = {
  manifestPath: "astrolift.toml",
  defaultBranch: "main",
  manifestRaw: VALID_MANIFEST,
  manifestFromRepo: true,
  manifestValid: true,
  manifestErrors: [],
  manifestLater: false,
};
