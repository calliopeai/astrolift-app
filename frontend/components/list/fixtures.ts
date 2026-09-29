/**
 * Story and test fixtures for the list archetype: a runs-like list (cursor
 * paged, live, bulk Retry and Cancel) and a members-like list (numbered,
 * CSV export). Data only; the columns live with the stories.
 */
import { type ListDefinition, standardViews } from "./list-state";

// ---------------------------------------------------------------------------
// Runs: Agents › Runs (spec 44 §4.4)
// ---------------------------------------------------------------------------

export type RunStatus = "running" | "succeeded" | "failed" | "waiting" | "scheduled";

export interface RunRow {
  id: string;
  agent: string;
  status: RunStatus;
  took: string;
  trigger: "webhook" | "schedule" | "manual" | "api";
  started: string;
  startedBy: string;
  project: string;
}

const RUN_STATUSES: RunStatus[] = ["running", "succeeded", "failed", "waiting", "scheduled"];

export const RUNS_LIST: ListDefinition = {
  id: "fixtures.runs",
  fields: [
    {
      key: "status",
      label: "Status",
      options: RUN_STATUSES.map((s) => ({ value: s, label: s })),
    },
    {
      key: "agent",
      label: "Agent",
      async: async (q) =>
        ["support-bot", "nightly-sync", "triage-agent", "billing-reconciler"]
          .filter((a) => a.includes(q.toLowerCase()))
          .map((a) => ({ value: a, label: a })),
    },
    {
      key: "trigger",
      label: "Trigger",
      options: ["webhook", "schedule", "manual", "api"].map((t) => ({ value: t, label: t })),
    },
    { key: "project", label: "Project" },
  ],
  searchPlaceholder: "Search runs, ids…",
  defaultSort: [{ key: "started", dir: "desc" }],
  views: standardViews({ startedBy: "me" }, [
    { key: "running", label: "Running", filters: { status: "running" } },
    { key: "failed", label: "Failed", filters: { status: "failed" } },
    { key: "waiting", label: "Waiting", filters: { status: "waiting" } },
    { key: "scheduled", label: "Scheduled", filters: { status: "scheduled" } },
  ]),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

const AGENTS = ["support-bot", "nightly-sync", "triage-agent", "billing-reconciler"];
const TRIGGERS: RunRow["trigger"][] = ["webhook", "schedule", "manual", "api"];

export const RUNS: RunRow[] = Array.from({ length: 25 }, (_, i) => ({
  id: `${(0x7f3c2a91 - i * 0x1b3d).toString(16)}-4e1a-9b2c-${String(1000 + i)}`,
  agent: AGENTS[i % AGENTS.length],
  status: RUN_STATUSES[(i * 3) % RUN_STATUSES.length],
  took: `${Math.floor((i * 37) / 60)}:${String((i * 37) % 60).padStart(2, "0")}`,
  trigger: TRIGGERS[i % TRIGGERS.length],
  started: `${2 + i * 7}m ago`,
  startedBy: i % 3 === 0 ? "ops@example.com" : "scheduler",
  project: i % 2 === 0 ? "storefront" : "internal-tools",
}));

/** Runs that arrived after the page was read, for the "new" pill. */
export const NEW_RUNS: RunRow[] = [
  { ...RUNS[0], id: "80a1c3d2-4e1a-9b2c-0999", started: "just now", status: "running" },
  { ...RUNS[1], id: "80a0f7e4-4e1a-9b2c-0998", started: "just now", status: "running" },
  { ...RUNS[2], id: "809f2a61-4e1a-9b2c-0997", started: "1m ago", status: "waiting" },
];

// ---------------------------------------------------------------------------
// Members: Admin › Members (numbered, CSV export)
// ---------------------------------------------------------------------------

export interface MemberRow {
  id: string;
  name: string;
  email: string;
  role: "owner" | "admin" | "member" | "viewer";
  team: string;
  lastSeen: string;
}

export const MEMBERS_LIST: ListDefinition = {
  id: "fixtures.members",
  fields: [
    {
      key: "role",
      label: "Role",
      options: ["owner", "admin", "member", "viewer"].map((r) => ({ value: r, label: r })),
    },
    { key: "team", label: "Team" },
  ],
  searchPlaceholder: "Search members, emails…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ invitedBy: "me" }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const ROLES: MemberRow["role"][] = ["owner", "admin", "member", "member", "viewer"];
const NAMES = [
  "Ada Lovelace",
  "Grace Hopper",
  "Katherine Johnson",
  "Margaret Hamilton",
  "Radia Perlman",
  "Barbara Liskov",
  "Frances Allen",
  "Hedy Lamarr",
];

export const MEMBERS: MemberRow[] = Array.from({ length: 25 }, (_, i) => {
  const name = NAMES[i % NAMES.length];
  return {
    id: `usr_${(1000 + i).toString(36)}`,
    name: i >= NAMES.length ? `${name} ${Math.floor(i / NAMES.length) + 1}` : name,
    email: `${name.toLowerCase().replace(/\s+/g, ".")}${i >= NAMES.length ? i : ""}@example.com`,
    role: ROLES[i % ROLES.length],
    team: i % 2 === 0 ? "platform" : "applications",
    lastSeen: `${1 + i}d ago`,
  };
});

export const MEMBERS_TOTAL = 140;

// ---------------------------------------------------------------------------
// Long strings: every list must hold these without widening the page.
// ---------------------------------------------------------------------------

export const LONG_SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
export const LONG_ARN =
  "arn:aws:iam::123456789012:role/astrolift/clusters/conflict-astrolift/workloads/agents/support-bot/" +
  "service-accounts/support-bot-runtime-executor/permission-boundaries/astrolift-agent-runtime-boundary-v2";
export const LONG_URL =
  "https://hooks.example.com/astrolift/webhooks/agents/support-bot/runs/trigger?token=eyJhbGciOiJIUzI1NiJ9eyJzdWIiOiJzdXBwb3J0LWJvdCJ9";

export const LONG_RUNS: RunRow[] = [
  { ...RUNS[0], id: LONG_SHA, agent: LONG_ARN, project: LONG_URL },
  { ...RUNS[1], id: LONG_SHA.slice(0, 40), agent: "support-bot", project: LONG_URL },
  ...RUNS.slice(2, 5),
];
