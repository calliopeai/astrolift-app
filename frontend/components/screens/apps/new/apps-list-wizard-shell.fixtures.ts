import type { RowSelection } from "@/components/data-table";
import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import type { AppFreshnessRowProps } from "../list/AppFreshnessRow";
import type { AppsListScreenProps, PushSecretsDialogProps } from "../list/AppsListScreen";

import type { AppDetailsStepViewProps } from "./AppDetailsStep";
import type { AppDetailsFields } from "./use-app-details-step";
import type { WizardShellProps, WizardStep } from "./WizardShell";

/**
 * Hand-typed fixtures for the apps list (components/screens/apps/list/) and
 * the register-app wizard shell + details step (components/screens/apps/new/).
 */

const noop = () => {};
const asyncNoop = async () => {};

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

// ---------------------------------------------------------------- list

const ORG = { id: "org-1", slug: "conflict", name: "CONFLICT" };

function app(overrides: Partial<AstroliftRegisteredApp>): AstroliftRegisteredApp {
  return {
    id: "a0000000-0000-4000-8000-000000000001",
    slug: "api-gateway",
    name: "API Gateway",
    description: "Edge router for every public API, with rate limits and auth.",
    teamSlug: "platform",
    projectSlug: "core",
    organizationSlug: ORG.slug,
    sourceKind: "github",
    sourceRepo: "conflict/api-gateway",
    deployBranch: "main",
    defaultBranch: "main",
    provisioningStatus: "ready",
    isActive: true,
    activePreviewCount: 0,
    lastDeployedAt: "2026-09-28T09:40:00Z",
    healthPulse: { status: "OK", message: "latest deploy running", ageSeconds: 1200 },
    latestDeployment: {
      id: "d0000000-0000-4000-8000-000000000001",
      status: "running",
      imageTag: "sha-4f2a9c1",
      commitSha: "4f2a9c1",
      environmentName: "prod",
      triggeredBy: "leo",
      createdAt: "2026-09-28T09:38:00Z",
      startedAt: "2026-09-28T09:38:10Z",
      endedAt: "2026-09-28T09:40:00Z",
    },
    ...overrides,
  } as unknown as AstroliftRegisteredApp;
}

export const APPS: AstroliftRegisteredApp[] = [
  app({}),
  app({
    id: "a0000000-0000-4000-8000-000000000002",
    slug: "billing-worker",
    name: "Billing Worker",
    description: "",
    activePreviewCount: 2,
    healthPulse: { status: "DEGRADED", message: "latest deploy failed 5m ago", ageSeconds: 300 },
    latestDeployment: {
      id: "d0000000-0000-4000-8000-000000000002",
      status: "failed",
      imageTag: "sha-91be004",
      commitSha: "91be004",
      environmentName: "prod",
      triggeredBy: "ci",
      createdAt: "2026-09-28T10:02:00Z",
      startedAt: "2026-09-28T10:02:05Z",
      endedAt: "2026-09-28T10:04:00Z",
    } as AstroliftRegisteredApp["latestDeployment"],
  }),
  app({
    id: "a0000000-0000-4000-8000-000000000003",
    slug: "docs-site",
    name: "Docs Site",
    projectSlug: "web",
    teamSlug: "growth",
    provisioningStatus: "provisioning",
    activePreviewCount: 1,
    lastDeployedAt: "2026-08-30T12:00:00Z",
    healthPulse: { status: "STALE", message: "no deploy in 29 days", ageSeconds: 2505600 },
  }),
  app({
    id: "a0000000-0000-4000-8000-000000000004",
    slug: "scratch",
    name: "Scratch",
    sourceRepo: "",
    sourceKind: "git_url",
    provisioningStatus: "failed",
    isActive: false,
    lastDeployedAt: null,
    latestDeployment: null,
    healthPulse: { status: "NEVER", message: "no deploys yet", ageSeconds: null },
  }),
];

export const LONG_APP = app({
  id: "a0000000-0000-4000-8000-000000000099",
  slug: LONG.slice(0, 40),
  name: `${LONG} ${LONG}`,
  description: `${LONG} ${LONG} ${LONG} ${LONG}`,
  teamSlug: LONG.slice(0, 30),
  projectSlug: LONG.slice(0, 30),
  sourceRepo: `conflict/${LONG}`,
  deployBranch: `release/${LONG}`,
  activePreviewCount: 12,
});

function selectionOf(ids: string[]): RowSelection {
  const set = new Set(ids);
  return {
    selectedIds: ids,
    selectedCount: ids.length,
    isSelected: (id) => set.has(id),
    toggle: noop,
    togglePage: noop,
    pageSelectionState: (page) => {
      const on = page.filter((id) => set.has(id)).length;
      if (on === 0) return false;
      return on === page.length ? true : "indeterminate";
    },
    clear: noop,
  };
}

export function appsListProps(
  rows: AstroliftRegisteredApp[],
  overrides: Partial<AppsListScreenProps> = {},
  controller: Parameters<typeof fakeController<AstroliftRegisteredApp>>[0] = {}
): AppsListScreenProps {
  const table = fakeController<AstroliftRegisteredApp>({
    rows,
    state: rows.length ? "ready" : "empty",
    totalCount: rows.length,
    sort: undefined,
    sortEnabled: false,
    ...controller,
  });
  return {
    viewMode: "card",
    setViewMode: noop,
    canDeploy: true,
    pill: "all",
    setPill: noop,
    sortBy: "CREATED_DESC",
    setSortBy: noop,
    narrowed: false,
    hasActiveFilters: false,
    clearFilters: noop,
    table,
    pinnedController: table,
    apps: table.rows,
    pinnedSet: new Set(["billing-worker"]),
    togglePin: noop,
    selection: selectionOf([]),
    bulkBusy: false,
    handleRollingRestart: asyncNoop,
    handlePushSecrets: asyncNoop,
    handleResyncManifest: asyncNoop,
    pushSecretsOpen: false,
    setPushSecretsOpen: noop,
    ...overrides,
  };
}

export const SELECTED = selectionOf(["api-gateway", "billing-worker"]);

export const PUSH_SECRETS: PushSecretsDialogProps = {
  open: true,
  onOpenChange: noop,
  appCount: 2,
  busy: false,
  onSubmit: asyncNoop,
};

export const FRESHNESS: Record<"ok" | "degraded" | "stale" | "never", AppFreshnessRowProps> = {
  ok: {
    pulse: APPS[0].healthPulse,
    latestDeployment: APPS[0].latestDeployment,
    lastDeployedAt: APPS[0].lastDeployedAt ?? null,
  },
  degraded: {
    pulse: APPS[1].healthPulse,
    latestDeployment: APPS[1].latestDeployment,
    lastDeployedAt: APPS[1].lastDeployedAt ?? null,
  },
  stale: {
    pulse: APPS[2].healthPulse,
    latestDeployment: APPS[2].latestDeployment,
    lastDeployedAt: APPS[2].lastDeployedAt ?? null,
  },
  never: { pulse: APPS[3].healthPulse, latestDeployment: null, lastDeployedAt: null },
};

// ---------------------------------------------------------------- wizard

export const WIZARD_STEPS: WizardStep[] = [
  { key: "repo", label: "Repository", description: "Pick the Git repository to deploy." },
  { key: "manifest", label: "Manifest", description: "Review or write astrolift.toml." },
  { key: "details", label: "Details", description: "Name the app and pick its project." },
  { key: "strategy", label: "Deploy strategy", description: "How and when the app deploys." },
  { key: "review", label: "Review", description: "Check everything, then register." },
];

export const WIZARD: Omit<WizardShellProps, "children"> = {
  step: 3,
  steps: WIZARD_STEPS,
  onStepClick: noop,
  onBack: noop,
  onNext: noop,
  onCancel: noop,
};

export const LONG_WIZARD_STEPS: WizardStep[] = WIZARD_STEPS.map((s) => ({
  ...s,
  label: `${s.label} ${LONG}`,
  description: `${s.description} ${LONG} ${LONG}`,
}));

const TEAMS: AstroliftTeam[] = [
  { id: "t1", slug: "platform", name: "Platform", organization: ORG },
  { id: "t2", slug: "growth", name: "Growth", organization: ORG },
] as unknown as AstroliftTeam[];

const PROJECTS: AstroliftProject[] = [
  {
    id: "p1",
    slug: "core",
    name: "Core services",
    organization: ORG,
    team: { id: "t1", slug: "platform", name: "Platform" },
  },
  {
    id: "p2",
    slug: "web",
    name: "Web properties",
    organization: ORG,
    team: { id: "t2", slug: "growth", name: "Growth" },
  },
] as unknown as AstroliftProject[];

export const DETAILS_STATE: AppDetailsFields = {
  name: "API Gateway",
  slug: "api-gateway",
  slugTouched: false,
  description: "Edge router for every public API.",
  projectId: "p1",
};

export const DETAILS: Omit<AppDetailsStepViewProps<AppDetailsFields>, "state" | "setState"> = {
  allTeams: TEAMS,
  allProjects: PROJECTS,
  slugValid: true,
};

export const LONG_DETAILS_STATE: AppDetailsFields = {
  name: LONG,
  slug: LONG.slice(0, 40),
  slugTouched: true,
  description: `${LONG} ${LONG} ${LONG}`,
  projectId: "p-long",
};

export const LONG_DETAILS: typeof DETAILS = {
  allTeams: [
    ...TEAMS,
    { id: "t-long", slug: LONG.slice(0, 40), name: LONG, organization: ORG },
  ] as unknown as AstroliftTeam[],
  allProjects: [
    ...PROJECTS,
    {
      id: "p-long",
      slug: LONG.slice(0, 40),
      name: LONG,
      organization: ORG,
      team: { id: "t-long", slug: LONG.slice(0, 40), name: LONG },
    },
  ] as unknown as AstroliftProject[],
  slugValid: true,
};
