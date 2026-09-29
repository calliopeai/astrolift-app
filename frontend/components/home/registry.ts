/**
 * Home's registries (spec 44 §4.3, decisions 4 and 7): one Home, built from
 * one set of panels; a layout is an arrangement of them. A new layout (an
 * approver's, a finance view) is one entry in HOME_LAYOUTS plus any new
 * panels in HOME_PANELS, with no change to Home itself: the Layout menu and
 * the first-sign-in question list whatever `offeredLayouts` returns.
 *
 * Only what the person may see: a layout whose `requires` they fail is not
 * offered, and a panel whose `requires` they fail is not drawn. The server
 * answers the yes and no (`me.modules`, `astroliftMyPermissions`); this file
 * only decides what to render from those answers.
 *
 * A panel names its component (components/home/panels/), each with its own
 * hook and its own query (rule 2), a ListSummary when it is a list (rule 3)
 * and a Feed when it grows (rule 5); `kind` records which. One without a
 * component yet is `PlaceholderPanel`.
 */

import type * as React from "react";

import type { PanelSpan } from "@/components/panel/Panel";
import type { ModuleKey } from "@/graphql/user/user.hooks";
import { type PermissionCheck, permissionMatches } from "@/lib/permissions/astrolift-permissions";

import { ActivityPanel } from "./panels/ActivityPanel";
import { AgentRunsPanel } from "./panels/AgentRunsPanel";
import { AlertsPanel } from "./panels/AlertsPanel";
import { ClustersPanel } from "./panels/ClustersPanel";
import { FailedRunsPanel } from "./panels/FailedRunsPanel";
import { FailingPanel } from "./panels/FailingPanel";
import { KpisPanel } from "./panels/KpisPanel";
import { MyAgentsPanel } from "./panels/MyAgentsPanel";
import { MyAppsPanel } from "./panels/MyAppsPanel";
import { PlatformActivityPanel } from "./panels/PlatformActivityPanel";
import { RecentDeploymentsPanel } from "./panels/RecentDeploymentsPanel";
import { RunningNowPanel } from "./panels/RunningNowPanel";
import { RunsSpendPanel } from "./panels/RunsSpendPanel";
import { SpendQuotaPanel } from "./panels/SpendQuotaPanel";
import { TrafficErrorsPanel } from "./panels/TrafficErrorsPanel";
import { WaitingPanel } from "./panels/WaitingPanel";
import { PlaceholderPanel } from "./PlaceholderPanel";

/** What Home knows about the viewer: the modules they may view and the permissions they hold. */
export interface HomeAccess {
  modules: ReadonlySet<ModuleKey>;
  permissions: ReadonlySet<string>;
}

/** A module check, shaped like `PermissionCheck`. */
export type ModuleCheck = ModuleKey | { anyOf: ModuleKey[] } | { allOf: ModuleKey[] };

/** Both halves must pass; an absent half passes. */
export interface HomeRequires {
  modules?: ModuleCheck;
  permissions?: PermissionCheck;
}

/**
 * What a panel is, which decides the primitive it is built from: a `list` is
 * a ListSummary, a `feed` is a Feed, `kpis` a strip of counts, `chart` a
 * trend with its legend.
 */
export type HomePanelKind = "list" | "feed" | "kpis" | "chart";

export const HOME_PANEL_KEYS = [
  "waiting",
  "failing",
  "my-apps",
  "recent-deployments",
  "traffic-errors",
  "failed-runs",
  "running-now",
  "my-agents",
  "runs-spend",
  "activity",
  "kpis",
  "deployments",
  "agent-runs",
  "alerts",
  "clusters",
  "platform-activity",
  "spend-quota",
] as const;

export type HomePanelKey = (typeof HOME_PANEL_KEYS)[number];

export interface HomePanelProps {
  panel: HomePanelDef;
}

export interface HomePanelDef {
  key: HomePanelKey;
  title: string;
  requires: HomeRequires;
  /** Columns of PanelGrid's 12 from `xl` up; everything stacks below. */
  span: PanelSpan;
  kind: HomePanelKind;
  /** The full list or page the panel summarizes: its View all. */
  href: string;
  component: React.ComponentType<HomePanelProps>;
}

export const HOME_LAYOUT_KEYS = ["apps", "agents", "builder", "operator"] as const;

export type HomeLayoutKey = (typeof HOME_LAYOUT_KEYS)[number];

export interface HomeLayoutDef {
  key: HomeLayoutKey;
  title: string;
  /** One sentence: who the layout is for. Shown in the Layout menu and the picker. */
  description: string;
  requires: HomeRequires;
  panels: HomePanelKey[];
}

const panel = (
  def: Omit<HomePanelDef, "component"> & { component?: HomePanelDef["component"] }
): HomePanelDef => ({
  component: PlaceholderPanel,
  ...def,
});

/**
 * Spans: each layout is a row of thirds (4 + 4 + 4) and a row of halves
 * (6 + 6), plus Builder's KPI strip, so a layout fits about two screens at
 * 1440x900 (rule 1).
 */
export const HOME_PANELS: Record<HomePanelKey, HomePanelDef> = {
  waiting: panel({
    key: "waiting",
    component: WaitingPanel,
    title: "Waiting on you",
    requires: {},
    span: 4,
    kind: "list",
    href: "/approvals",
  }),
  failing: panel({
    key: "failing",
    component: FailingPanel,
    title: "Failing",
    requires: { modules: { anyOf: ["apps", "agents"] } },
    span: 4,
    kind: "list",
    href: "/deployments?view=failed",
  }),
  "my-apps": panel({
    key: "my-apps",
    component: MyAppsPanel,
    title: "My apps",
    requires: { modules: "apps", permissions: "app.read" },
    span: 4,
    kind: "list",
    href: "/apps?view=mine",
  }),
  "recent-deployments": panel({
    key: "recent-deployments",
    component: RecentDeploymentsPanel,
    title: "Recent deployments",
    requires: { modules: "apps", permissions: "app.read" },
    span: 6,
    kind: "list",
    href: "/deployments",
  }),
  "traffic-errors": panel({
    key: "traffic-errors",
    component: TrafficErrorsPanel,
    title: "Traffic & errors",
    requires: { modules: "apps", permissions: "app.read_metrics" },
    span: 6,
    kind: "chart",
    href: "/apps",
  }),
  "failed-runs": panel({
    key: "failed-runs",
    component: FailedRunsPanel,
    title: "Failed runs",
    requires: { modules: "agents", permissions: "agent.read" },
    span: 4,
    kind: "list",
    href: "/tasks?view=failed",
  }),
  "running-now": panel({
    key: "running-now",
    component: RunningNowPanel,
    title: "Running now",
    requires: { modules: "agents", permissions: "agent.read" },
    span: 4,
    kind: "list",
    href: "/tasks?view=running",
  }),
  "my-agents": panel({
    key: "my-agents",
    component: MyAgentsPanel,
    title: "My agents",
    requires: { modules: "agents", permissions: "agent.read" },
    span: 6,
    kind: "list",
    href: "/agents?view=mine",
  }),
  "runs-spend": panel({
    key: "runs-spend",
    component: RunsSpendPanel,
    title: "Runs & spend",
    requires: { modules: "agents", permissions: "agent.read" },
    span: 6,
    kind: "chart",
    href: "/tasks",
  }),
  activity: panel({
    key: "activity",
    component: ActivityPanel,
    title: "Activity",
    requires: { permissions: "audit_log.read" },
    span: 4,
    kind: "feed",
    href: "/administration/audit",
  }),
  kpis: panel({
    key: "kpis",
    component: KpisPanel,
    title: "KPIs",
    requires: { modules: { anyOf: ["apps", "agents"] } },
    span: 12,
    kind: "kpis",
    href: "/administration/metrics",
  }),
  deployments: panel({
    key: "deployments",
    component: RecentDeploymentsPanel,
    title: "Deployments",
    requires: { modules: "apps", permissions: "app.read" },
    span: 6,
    kind: "list",
    href: "/deployments",
  }),
  "agent-runs": panel({
    key: "agent-runs",
    component: AgentRunsPanel,
    title: "Agent runs",
    requires: { modules: "agents", permissions: "agent.read" },
    span: 6,
    kind: "list",
    href: "/tasks?kind=agent",
  }),
  alerts: panel({
    key: "alerts",
    component: AlertsPanel,
    title: "Alerts",
    requires: { modules: "admin" },
    span: 4,
    kind: "list",
    href: "/alerts/events?view=firing",
  }),
  clusters: panel({
    key: "clusters",
    component: ClustersPanel,
    title: "Clusters",
    requires: { modules: "admin" },
    span: 4,
    kind: "list",
    href: "/clusters",
  }),
  "platform-activity": panel({
    key: "platform-activity",
    component: PlatformActivityPanel,
    title: "Platform activity",
    requires: { modules: "admin", permissions: "audit_log.read" },
    span: 6,
    kind: "feed",
    href: "/platform-activity",
  }),
  "spend-quota": panel({
    key: "spend-quota",
    component: SpendQuotaPanel,
    title: "Spend & quota",
    requires: { permissions: "billing.read" },
    span: 6,
    kind: "chart",
    href: "/administration/metrics",
  }),
};

/** The four layouts, in the order the menu and the picker list them. */
export const HOME_LAYOUTS: Record<HomeLayoutKey, HomeLayoutDef> = {
  apps: {
    key: "apps",
    title: "Apps",
    description: "For running applications: failing deploys, your apps and their traffic.",
    requires: { modules: "apps" },
    panels: ["waiting", "failing", "my-apps", "recent-deployments", "traffic-errors"],
  },
  agents: {
    key: "agents",
    title: "Agents",
    description: "For building and running agents: failed and live runs, your agents and spend.",
    requires: { modules: "agents" },
    panels: ["waiting", "failed-runs", "running-now", "my-agents", "runs-spend"],
  },
  builder: {
    key: "builder",
    title: "Builder",
    description: "For both, hands on: what is failing, what just happened, deploys and runs.",
    requires: { modules: { allOf: ["apps", "agents"] } },
    panels: ["waiting", "failing", "activity", "kpis", "deployments", "agent-runs"],
  },
  operator: {
    key: "operator",
    title: "Operator",
    description: "For keeping the platform running: alerts, clusters, platform activity and quota.",
    requires: { modules: "admin" },
    panels: ["alerts", "clusters", "waiting", "platform-activity", "spend-quota"],
  },
};

export function isHomeLayoutKey(value: unknown): value is HomeLayoutKey {
  return typeof value === "string" && (HOME_LAYOUT_KEYS as readonly string[]).includes(value);
}

function moduleMatches(modules: ReadonlySet<ModuleKey>, check: ModuleCheck): boolean {
  if (typeof check === "string") return modules.has(check);
  if ("anyOf" in check) return check.anyOf.some((m) => modules.has(m));
  return check.allOf.every((m) => modules.has(m));
}

/** True when the viewer passes both halves of `requires`. */
export function meets(requires: HomeRequires, access: HomeAccess): boolean {
  return (
    (requires.modules === undefined || moduleMatches(access.modules, requires.modules)) &&
    (requires.permissions === undefined ||
      permissionMatches(access.permissions, requires.permissions))
  );
}

/** The layouts the viewer is offered, in registry order. */
export function offeredLayouts(access: HomeAccess): HomeLayoutDef[] {
  return HOME_LAYOUT_KEYS.map((k) => HOME_LAYOUTS[k]).filter((l) => meets(l.requires, access));
}

/** The layout's panels the viewer may see, in the layout's order. */
export function visiblePanels(layout: HomeLayoutDef, access: HomeAccess): HomePanelDef[] {
  return layout.panels.map((k) => HOME_PANELS[k]).filter((p) => meets(p.requires, access));
}

/**
 * The layout preselected from access: an admin or operator (the `admin`
 * module) gets Operator; both Apps and Agents, Builder; only one of them,
 * that one. Null when the viewer has none of those modules, and so is
 * offered no layout.
 */
export function defaultLayoutFor(access: HomeAccess): HomeLayoutKey | null {
  if (access.modules.has("admin")) return "operator";
  const apps = access.modules.has("apps");
  const agents = access.modules.has("agents");
  if (apps && agents) return "builder";
  if (apps) return "apps";
  if (agents) return "agents";
  return null;
}

/**
 * The layout Home draws: the saved one while it is still offered (a module
 * can be taken away after it was chosen), otherwise the access default.
 */
export function resolveLayout(
  saved: HomeLayoutKey | null,
  access: HomeAccess
): HomeLayoutDef | null {
  if (saved && meets(HOME_LAYOUTS[saved].requires, access)) return HOME_LAYOUTS[saved];
  const fallback = defaultLayoutFor(access);
  return fallback ? HOME_LAYOUTS[fallback] : null;
}
