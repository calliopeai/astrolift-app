/**
 * The main rail's navigation (spec 44 §4.1, §4.4): areas, and under each the
 * area's functions. Pure data plus two pure functions, so the rail, the
 * breadcrumb switcher and the tests all read the same model.
 *
 * Visibility is the server's answer (`me.modules.<key>.canView`, spec 36):
 * a function carries the module that gates it, and an area shows when any of
 * its functions do. Workflows and Functions sit in the Agents area but stay
 * gated by the workflows module, so an install entitled to one and not the
 * other sees exactly what it did before.
 */
import {
  ActivityIcon,
  BellIcon,
  BookOpenIcon,
  BotIcon,
  BoltIcon,
  BoxesIcon,
  BrainCircuitIcon,
  BuildingIcon,
  CalendarClockIcon,
  ClipboardListIcon,
  CloudIcon,
  EyeIcon,
  FlagIcon,
  FolderIcon,
  GaugeIcon,
  GlobeIcon,
  HistoryIcon,
  HomeIcon,
  KeyRoundIcon,
  LayersIcon,
  type LucideIcon,
  PackageIcon,
  RocketIcon,
  ScrollTextIcon,
  ServerIcon,
  SettingsIcon,
  UsersIcon,
  WebhookIcon,
  WorkflowIcon,
  WrenchIcon,
} from "lucide-react";

export type ModuleKey = "apps" | "agents" | "workflows" | "admin";

export interface NavFunction {
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
  /** The module whose `canView` shows this row; none means always shown. */
  module?: ModuleKey;
  /** Anchor for the first-run spotlight tour (`data-onboarding-tour`). */
  tourTarget?: string;
  /** Other path prefixes this row owns (a function whose pages sit under several routes). */
  also?: string[];
}

export interface NavGroup {
  label?: string;
  functions: NavFunction[];
}

export interface NavArea {
  key: "home" | "agents" | "apps" | "admin";
  label: string;
  icon: LucideIcon;
  groups: NavGroup[];
}

export const NAV: NavArea[] = [
  {
    key: "home",
    label: "Home",
    icon: HomeIcon,
    groups: [
      {
        functions: [
          {
            key: "home",
            label: "Home",
            href: "/dashboard",
            icon: HomeIcon,
            tourTarget: "dashboard-nav",
          },
        ],
      },
    ],
  },
  {
    key: "agents",
    label: "Agents",
    icon: BotIcon,
    groups: [
      {
        functions: [
          { key: "agents", label: "Agents", href: "/agents", icon: BotIcon, module: "agents" },
          {
            key: "workflows",
            label: "Workflows",
            href: "/workflows",
            icon: WorkflowIcon,
            module: "workflows",
          },
          { key: "runs", label: "Runs", href: "/tasks", icon: ClipboardListIcon, module: "agents" },
          {
            key: "functions",
            label: "Functions",
            href: "/functions",
            icon: BoltIcon,
            module: "workflows",
          },
          {
            key: "skills",
            label: "Skills",
            href: "/agents/skills",
            icon: BookOpenIcon,
            module: "agents",
          },
          {
            key: "tools",
            label: "Tools",
            href: "/agents/tools",
            icon: WrenchIcon,
            module: "agents",
          },
          {
            key: "environment-specs",
            label: "Environment specs",
            href: "/agents/environment-specs",
            icon: BoxesIcon,
            module: "agents",
          },
          {
            key: "models",
            label: "Models",
            href: "/models",
            icon: BrainCircuitIcon,
            module: "agents",
          },
          {
            // The runtime units behind agents, workflows and functions
            // (spec 44 §10.2); an app's own workloads stay on its tab.
            key: "workloads",
            label: "Workloads",
            href: "/workloads",
            icon: BoxesIcon,
            module: "agents",
          },
        ],
      },
    ],
  },
  {
    key: "apps",
    label: "Apps",
    icon: PackageIcon,
    groups: [
      {
        functions: [
          { key: "apps", label: "Apps", href: "/apps", icon: PackageIcon, module: "apps" },
          {
            key: "deployments",
            label: "Deployments",
            href: "/deployments",
            icon: RocketIcon,
            module: "apps",
          },
          {
            key: "jobs",
            label: "Scheduled jobs",
            href: "/jobs",
            icon: CalendarClockIcon,
            module: "apps",
          },
          {
            key: "environments",
            label: "Environments",
            href: "/environments",
            icon: LayersIcon,
            module: "apps",
          },
          { key: "previews", label: "Previews", href: "/previews", icon: EyeIcon, module: "apps" },
        ],
      },
    ],
  },
  {
    key: "admin",
    label: "Admin",
    icon: SettingsIcon,
    groups: [
      {
        label: "Organization",
        functions: [
          {
            key: "organization",
            label: "Organization",
            href: "/administration/organization",
            icon: BuildingIcon,
            module: "admin",
          },
          {
            key: "access",
            label: "Access",
            href: "/administration/access",
            icon: UsersIcon,
            module: "admin",
            also: ["/administration/permissions", "/administration/policies"],
          },
          {
            key: "projects",
            label: "Projects",
            href: "/administration/projects",
            icon: FolderIcon,
            module: "admin",
          },
        ],
      },
      {
        label: "Infrastructure",
        functions: [
          {
            key: "clusters",
            label: "Clusters",
            href: "/clusters",
            icon: ServerIcon,
            module: "admin",
            tourTarget: "clusters-nav",
          },
          { key: "domains", label: "Domains", href: "/domains", icon: GlobeIcon, module: "admin" },
          {
            key: "providers",
            label: "Providers",
            href: "/providers",
            icon: CloudIcon,
            module: "admin",
          },
          {
            key: "webhooks",
            label: "Webhooks",
            href: "/webhooks",
            icon: WebhookIcon,
            module: "admin",
          },
        ],
      },
      {
        label: "Usage & governance",
        functions: [
          {
            key: "metrics",
            label: "Metrics",
            href: "/administration/metrics",
            icon: GaugeIcon,
            module: "admin",
          },
          {
            key: "tokens",
            label: "API keys",
            href: "/tokens",
            icon: KeyRoundIcon,
            module: "admin",
          },
          {
            key: "audit",
            label: "Audit",
            href: "/administration/audit",
            icon: ScrollTextIcon,
            module: "admin",
          },
          {
            // The combined run audit (spec 44 §4.4, decision 14): agent and
            // workflow runs, deployments and job runs in one list. Keyed apart
            // from the Agents area's own Runs.
            key: "run-audit",
            label: "Runs",
            href: "/administration/runs",
            icon: HistoryIcon,
            module: "admin",
          },
          {
            key: "features",
            label: "Features",
            href: "/administration/features",
            icon: FlagIcon,
            module: "admin",
          },
        ],
      },
      {
        label: "Platform signals",
        functions: [
          {
            key: "activity",
            label: "Platform activity",
            href: "/platform-activity",
            icon: ActivityIcon,
            module: "admin",
          },
          { key: "alerts", label: "Alerts", href: "/alerts", icon: BellIcon, module: "admin" },
          { key: "events", label: "Events", href: "/events", icon: ActivityIcon, module: "admin" },
        ],
      },
    ],
  },
];

/** The model with every function the viewer may not see removed, and empty areas dropped. */
export function visibleNav(nav: NavArea[], canView: (module: ModuleKey) => boolean): NavArea[] {
  return nav
    .map((area) => ({
      ...area,
      groups: area.groups
        .map((g) => ({
          ...g,
          functions: g.functions.filter((f) => !f.module || canView(f.module)),
        }))
        .filter((g) => g.functions.length > 0),
    }))
    .filter((area) => area.groups.length > 0);
}

/**
 * Which area and function own `pathname`: the function whose href is the
 * longest prefix of it, so /agents/skills/42 is Skills, not Agents.
 */
export function activeFor(
  nav: NavArea[],
  pathname: string
): { area: NavArea["key"]; fn: string } | null {
  let best: { area: NavArea["key"]; fn: string; len: number } | null = null;
  for (const area of nav) {
    for (const group of area.groups) {
      for (const f of group.functions) {
        for (const prefix of [f.href, ...(f.also ?? [])]) {
          const matches = pathname === prefix || pathname.startsWith(`${prefix}/`);
          if (matches && (!best || prefix.length > best.len)) {
            best = { area: area.key, fn: f.key, len: prefix.length };
          }
        }
      }
    }
  }
  return best ? { area: best.area, fn: best.fn } : null;
}

/**
 * The first crumb of a page inside an area (spec 44 §4.4 rule 3): the area's
 * name, switching between its functions, with `active` checked. Read from
 * this model so the rail and the breadcrumb always agree (rule 7).
 */
export function areaSwitcher(
  nav: NavArea[],
  area: NavArea["key"],
  active: string
): { label: string; switcher: { label: string; href: string; active: boolean }[] } {
  const found = nav.find((a) => a.key === area);
  return {
    label: found?.label ?? area,
    switcher: (found?.groups ?? []).flatMap((g) =>
      g.functions.map((f) => ({ label: f.label, href: f.href, active: f.key === active }))
    ),
  };
}
