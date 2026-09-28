import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftTeam } from "@/graphql/identity/identity.types";
import type { AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

import type { AssignProjectCardViewProps } from "./AssignProjectCard";
import type { TeamsCardViewProps } from "./TeamsCard";
import type { UptimeCardViewProps } from "./UptimeCard";
import type { UrlCardViewProps } from "./UrlCard";
import type { UrlHealthBadgeViewProps } from "./UrlHealthBadge";
import type { AppUptime } from "./use-uptime";
import type { UrlHealthRow } from "./use-url-health";

/**
 * Hand-typed fixtures for the app overview cards: project assignment,
 * team access, uptime, public URL and the URL health pill.
 */

const noop = async () => {};
const yes = async () => true;

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

const ORG = { id: "org-1", slug: "acme", name: "Acme" };

/** An ISO timestamp `seconds` before now, so relative labels read sensibly. */
function ago(seconds: number) {
  return new Date(Date.now() - seconds * 1000).toISOString();
}

// ─── Assign project ──────────────────────────────────────────────────────────

export const ASSIGN_PROJECT: AssignProjectCardViewProps = {
  currentProjectId: "proj-1",
  currentProjectName: "Storefront",
  currentTeamName: "Platform",
  byTeam: [
    {
      teamName: "Payments",
      teamSlug: "payments",
      projects: [
        {
          id: "proj-3",
          slug: "billing",
          name: "Billing",
          team: { id: "t-2", slug: "payments", name: "Payments" },
        },
      ],
    },
    {
      teamName: "Platform",
      teamSlug: "platform",
      projects: [
        {
          id: "proj-2",
          slug: "internal-tools",
          name: "Internal tools",
          team: { id: "t-1", slug: "platform", name: "Platform" },
        },
        {
          id: "proj-1",
          slug: "storefront",
          name: "Storefront",
          team: { id: "t-1", slug: "platform", name: "Platform" },
        },
      ],
    },
  ],
  projectsLoading: false,
  assigning: false,
  onAssign: noop,
  onUnassign: noop,
};

// ─── Teams ───────────────────────────────────────────────────────────────────

function access(
  id: string,
  teamId: string,
  teamName: string,
  accessLevel: AstroliftAppTeamAccess["accessLevel"],
  isHome = false
): AstroliftAppTeamAccess {
  return {
    id,
    appId: "app-1",
    appSlug: "storefront",
    teamId,
    teamName,
    teamSlug: teamName.toLowerCase().replace(/\s+/g, "-"),
    accessLevel,
    isHome,
    createdAt: "2026-09-01T10:00:00Z",
    updatedAt: "2026-09-20T10:00:00Z",
  };
}

function team(id: string, name: string, slug: string): AstroliftTeam {
  return {
    id,
    name,
    slug,
    organization: ORG,
    createdAt: "2026-08-01T10:00:00Z",
    updatedAt: "2026-08-01T10:00:00Z",
  };
}

export const ACCESSES: AstroliftAppTeamAccess[] = [
  access("a-1", "t-1", "Platform", "owner", true),
  access("a-2", "t-2", "Payments", "deployer"),
  access("a-3", "t-3", "Data", "viewer"),
];

export const TEAMS: AstroliftTeam[] = [
  team("t-1", "Platform", "platform"),
  team("t-2", "Payments", "payments"),
  team("t-3", "Data", "data"),
  team("t-4", "Growth", "growth"),
  team("t-5", "Security", "security"),
];

export const TEAMS_CARD: TeamsCardViewProps = {
  homeTeamSlug: "platform",
  table: fakeController<AstroliftAppTeamAccess>({
    rows: ACCESSES,
    totalCount: ACCESSES.length,
    sort: undefined,
    sortEnabled: false,
  }),
  teams: TEAMS,
  teamsLoading: false,
  candidateTeams: TEAMS.slice(3),
  granting: false,
  revoking: false,
  moving: false,
  adding: false,
  onLevelChange: noop,
  onRevoke: noop,
  onMove: yes,
  onAddTeam: yes,
};

export const LONG_ACCESS = access("a-9", "t-9", LONG, "deployer");
export const LONG_TEAM = team("t-9", LONG, LONG);

// ─── Uptime ──────────────────────────────────────────────────────────────────

const LATENCIES = [82, 91, 77, 140, 88, 95, 102, 79, 84, 90, 210, 86];

export const UPTIME_DATA: AppUptime = {
  isUp: true,
  lastCheckedAt: ago(40),
  uptimePct: 99.4,
  totalChecks: 720,
  windowHours: 24,
  recent: LATENCIES.map((latencyMs, i) => ({
    checkedAt: ago(40 + i * 120),
    isUp: true,
    latencyMs,
    statusCode: 200,
  })),
};

export const UPTIME: UptimeCardViewProps = { uptime: UPTIME_DATA, loading: false };

// ─── URL health ──────────────────────────────────────────────────────────────

export const HEALTH_OK: UrlHealthRow = {
  url: "https://storefront.acme.astrolift.app/",
  status: "ok",
  statusCode: 200,
  latencyMs: 84,
  lastChecked: ago(12),
  message: "",
};

export const HEALTH_HISTORY: UrlHealthRow[] = [
  HEALTH_OK,
  { ...HEALTH_OK, latencyMs: 91, lastChecked: ago(42) },
  { ...HEALTH_OK, status: "degraded", statusCode: 503, latencyMs: 1204, lastChecked: ago(72) },
  { ...HEALTH_OK, latencyMs: 79, lastChecked: ago(102) },
  { ...HEALTH_OK, latencyMs: 88, lastChecked: ago(3700) },
];

export const URL_HEALTH: UrlHealthBadgeViewProps = {
  health: HEALTH_OK,
  isInitialLoading: false,
  rechecking: false,
  history: HEALTH_HISTORY,
  loadingHistory: false,
  onHistoryOpenChange: () => {},
  onRecheck: noop,
};

// ─── Public URL ──────────────────────────────────────────────────────────────

export const URL_CARD: Omit<UrlCardViewProps, "healthBadge"> = {
  subdomain: "storefront",
  fullHost: "storefront.acme.astrolift.app",
  isRoutable: true,
  isProvisioning: false,
  hasWorkload: true,
  saving: false,
  onSave: yes,
  onCopy: noop,
};
