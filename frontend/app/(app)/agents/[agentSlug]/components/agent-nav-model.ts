import {
  ActivityIcon,
  BoxesIcon,
  FileCodeIcon,
  KeyIcon,
  KeyRoundIcon,
  LayersIcon,
  RocketIcon,
  SettingsIcon,
  ShieldIcon,
  SlidersHorizontalIcon,
  UsersIcon,
  WebhookIcon,
} from "lucide-react";

/**
 * Shared nav taxonomy for the agent-detail shell.
 *
 * An agent IS a RegisteredApp, so it carries the same platform sub-pages the
 * app-detail surface does. `app-tabs.tsx` folds those pages into the five BROCS
 * pillars (Build · Run · Observe · Control · Secure); this mirrors that exact
 * seg→pillar mapping for the agent shell so the two surfaces stay consistent.
 *
 * The app surface renders both nav levels from a single component. The agent
 * shell splits them in two because its pillar bar links to agent-native pages,
 * not to the platform sub-pages:
 *   - `AgentTabs`        — the primary pillar bar. Adds an agent-native
 *                          `overview` landing pillar (which the app surface
 *                          folds into Run). Each pillar links to its own page at
 *                          `/agents/<slug>/<segment>`.
 *   - `AppPlatformLinks` — the secondary row: the ACTIVE pillar's platform
 *                          sub-pages only (previously one flat row repeated
 *                          under every pillar).
 *
 * Both read {@link AGENT_PILLARS} + {@link resolveActivePillar} from here so the
 * pillar bar and the sub-row always agree on the active pillar — including when
 * the URL is a platform sub-page (e.g. `…/config` resolves to Build).
 */

export type AgentPillarKey = "overview" | "build" | "run" | "observe" | "control" | "secure";

/**
 * A platform sub-page grouped under a pillar. Links to a real
 * `/agents/<agentSlug>/<seg>` route — the shared `/apps/<slug>/<seg>` client
 * mounted in agent context by `AgentAppSurface`.
 */
export interface AgentPlatformLink {
  seg: string;
  label: string;
  Icon: typeof RocketIcon;
}

export interface AgentNavPillar {
  key: AgentPillarKey;
  /** Pillar-bar display label. */
  label: string;
  /** Agent-native pillar page: `/agents/<agentSlug>/<segment>`. */
  segment: string;
  /** Platform sub-pages grouped under this pillar (the secondary row). */
  platform: AgentPlatformLink[];
}

/**
 * The six agent pillars and the platform sub-pages under each — the same
 * seg→pillar mapping `app-tabs.tsx` uses, minus segs that don't apply to an
 * agent:
 *   - Overview : agent-native landing (no platform sub-pages).
 *   - Build    : config · manifest · webhooks (CI/CD).
 *   - Run      : deployments · environments. (The app's Run pillar also lists
 *                jobs/commands/workloads/topology/previews — none of which have
 *                an agent route; its `overview` is the agent's own pillar here.)
 *   - Observe  : observability. (The app's `console` has no agent route.)
 *   - Control  : services · settings · members. Domains omitted — agents are
 *                task/serverless workloads, not HTTP services with domains.
 *   - Secure   : security · secrets · tokens.
 */
export const AGENT_PILLARS: readonly AgentNavPillar[] = [
  { key: "overview", label: "Overview", segment: "overview", platform: [] },
  {
    key: "build",
    label: "Build",
    segment: "build",
    platform: [
      { seg: "config", label: "Config", Icon: SlidersHorizontalIcon },
      { seg: "manifest", label: "Manifest", Icon: FileCodeIcon },
      { seg: "webhooks", label: "CI / CD", Icon: WebhookIcon },
    ],
  },
  {
    key: "run",
    label: "Run",
    segment: "run",
    platform: [
      { seg: "deployments", label: "Deployments", Icon: RocketIcon },
      { seg: "environments", label: "Environments", Icon: LayersIcon },
    ],
  },
  {
    key: "observe",
    label: "Observe",
    segment: "observe",
    platform: [{ seg: "observability", label: "Observability", Icon: ActivityIcon }],
  },
  {
    key: "control",
    label: "Control",
    segment: "control",
    platform: [
      { seg: "managed-services", label: "Services", Icon: BoxesIcon },
      { seg: "settings", label: "Settings", Icon: SettingsIcon },
      { seg: "members", label: "Members", Icon: UsersIcon },
    ],
  },
  {
    key: "secure",
    label: "Secure",
    segment: "secure",
    platform: [
      { seg: "security", label: "Security", Icon: ShieldIcon },
      { seg: "secrets", label: "Secrets", Icon: KeyRoundIcon },
      { seg: "tokens", label: "Tokens", Icon: KeyIcon },
    ],
  },
];

/**
 * `/agents/<agentSlug>` with the slug URL-encoded — matches `usePathname()` and
 * every agent `page.tsx`, which key off the `[agentSlug]` route param.
 */
export function agentBasePath(agentSlug: string): string {
  return `/agents/${encodeURIComponent(agentSlug)}`;
}

/** A route segment owns the pathname when the URL is its page or a child of it. */
function ownsPath(pathname: string, base: string, seg: string): boolean {
  const full = `${base}/${seg}`;
  return pathname === full || pathname.startsWith(`${full}/`);
}

/**
 * Resolve the active pillar from the pathname, defaulting to Overview (the
 * landing pillar). A pillar owns the path when the URL is its agent-native page
 * OR any of its platform sub-pages — so `…/config` resolves to Build, keeping
 * the pillar bar and the sub-row in agreement on a platform page.
 */
export function resolveActivePillar(pathname: string, agentSlug: string): AgentPillarKey {
  const base = agentBasePath(agentSlug);
  for (const pillar of AGENT_PILLARS) {
    if (ownsPath(pathname, base, pillar.segment)) return pillar.key;
    for (const link of pillar.platform) {
      if (ownsPath(pathname, base, link.seg)) return pillar.key;
    }
  }
  return "overview";
}
