import type { ListAuditEventsPageQuery } from "@/graphql/__generated__/operations";

import { HEALTHY, POSTGRES_SUBSTRATE } from "../brain-status.fixtures";
import type { BrainSubstrate, FramedBrain, HistoryEntry } from "./brain-detail-model";
import { historyEntry } from "./brain-detail-model";

const FILES: BrainSubstrate = { ...HEALTHY.substrate, adopted: null, graduationNeeded: null };

/** `astroliftBrain` for a served layer with shares and hosted apps. */
export const BRAIN: FramedBrain = {
  id: "b0000000-0000-4000-8000-000000000001",
  slug: "platform-brain",
  name: "Platform knowledge",
  kind: "mimwell",
  team: "platform",
  project: "core",
  environments: [
    { name: "production", url: "https://platform-brain.apps.example.test" },
    { name: "staging", url: "https://platform-brain.staging.example.test" },
  ],
  allEnvironments: [
    { name: "production", served: true },
    { name: "staging", served: true },
  ],
  sharedWith: [
    { team: "medops", access: "viewer" },
    { team: "sales", access: "deployer" },
    { team: "security", access: "owner" },
  ],
  hostedApps: [
    { slug: "brain-chat", environment: "production" },
    { slug: "brain-chat", environment: "staging" },
    { slug: "support-bot", environment: "production" },
  ],
  substrate: FILES,
};

export const ONE_ENV: FramedBrain = {
  ...BRAIN,
  environments: [BRAIN.environments[0]!],
  allEnvironments: [BRAIN.allEnvironments[0]!],
};

/** Registered and owned, nothing served yet, shared with no one. */
export const NOT_SERVED: FramedBrain = {
  ...BRAIN,
  slug: "draft-brain",
  name: "Draft layer",
  environments: [],
  allEnvironments: [],
  sharedWith: [],
  hostedApps: [],
};

export const ON_POSTGRES: FramedBrain = {
  ...BRAIN,
  substrate: { ...POSTGRES_SUBSTRATE, adopted: null, graduationNeeded: null },
};

export const LONG_BRAIN: FramedBrain = {
  ...BRAIN,
  slug: "a-very-long-semantic-layer-slug-that-keeps-going-for-the-layout-check",
  name: "A semantic layer with a name long enough to wrap in every locale the console ships",
  team: "platform-engineering-and-reliability",
  project: "knowledge-and-ontology-services",
};

type AuditRow = ListAuditEventsPageQuery["astroliftAuditEventsPage"]["items"][number];

function audit(patch: Partial<AuditRow>): AuditRow {
  return {
    id: "a0000000-0000-4000-8000-000000000001",
    organizationId: "o0000000-0000-4000-8000-000000000001",
    occurredAt: "2026-10-16T09:00:00Z",
    actorKind: "user",
    actorId: "7",
    actorDisplay: "ana@acme.example",
    action: "brain.share",
    decision: "ALLOW",
    targetKind: "RegisteredApp",
    targetId: "platform-brain",
    targetSlug: "platform-brain",
    requestId: "req-1",
    data: {
      surface: "graphql",
      shared_with: [
        { team: "medops", access: "viewer" },
        { team: "sales", access: "deployer" },
      ],
    },
    before: null,
    after: null,
    ...patch,
  };
}

/** The audit rows the history reads, newest first: a share, a refusal, an agent's unshare. */
export const AUDIT_ROWS: AuditRow[] = [
  audit({ id: "a0000000-0000-4000-8000-000000000003" }),
  audit({
    id: "a0000000-0000-4000-8000-000000000002",
    occurredAt: "2026-10-15T17:30:00Z",
    actorDisplay: "lee@acme.example",
    decision: "DENY",
    data: { surface: "graphql", error_code: "STEP_UP_REQUIRED" },
  }),
  audit({
    id: "a0000000-0000-4000-8000-000000000001",
    occurredAt: "2026-10-14T08:00:00Z",
    actorDisplay: "support-agent token",
    action: "brain.unshare",
    data: { surface: "mcp", token: "t-1", shared_with: [{ team: "medops", access: "viewer" }] },
  }),
];

export const HISTORY: HistoryEntry[] = AUDIT_ROWS.map(historyEntry);

export const TEAM_RESULTS = [
  { kind: "team" as const, id: "finance", name: "Finance" },
  { kind: "team" as const, id: "medops", name: "Medical operations", detail: "Shared as viewer" },
  { kind: "team" as const, id: "research", name: "Research" },
];
