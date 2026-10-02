import type { DetailTab } from "@/components/DetailPageTabs";
import {
  activeTabSection,
  type DetailTabSection,
  type DetailTabSpec,
  detailRedirectTarget,
  detailTabHref,
  detailTabs,
  resolveDetailTab,
  type SearchParams,
  sectionsBy,
  tabSectionHref,
} from "@/components/detail/detail-tabs-model";

/**
 * The agent detail's one row of tabs, named by function (spec 44 §5.2,
 * §9 step 5): Overview · Runs · Configuration · Skills & tools ·
 * Logs & metrics · Secrets · Access · Settings. Each tab is its own route
 * under `/agents/[agentSlug]`; the former BROCS pillar pages and the app
 * platform pages an agent carried (`owns`) redirect into the tab that took
 * them, and still light it up. Labels default to English metadata; the connected frame supplies localized labels.
 */
export type AgentTabKey =
  | "overview"
  | "runs"
  | "configuration"
  | "skills"
  | "logs"
  | "secrets"
  | "access"
  | "settings";

export const AGENT_BASE = "/agents";

export const AGENT_TABS: readonly DetailTabSpec<AgentTabKey>[] = [
  { key: "overview", label: "Overview", segment: "", owns: ["overview"] },
  { key: "runs", label: "Runs", segment: "runs", owns: ["run"] },
  {
    key: "configuration",
    label: "Configuration",
    segment: "configuration",
    owns: ["build", "control", "config", "manifest", "webhooks"],
  },
  { key: "skills", label: "Skills & tools", segment: "skills", owns: [] },
  {
    key: "logs",
    label: "Logs & metrics",
    segment: "logs",
    owns: ["observe", "observability", "deployments"],
  },
  { key: "secrets", label: "Secrets", segment: "secrets", owns: [] },
  {
    key: "access",
    label: "Access",
    segment: "access",
    owns: ["members", "tokens", "security", "secure"],
  },
  {
    key: "settings",
    label: "Settings",
    segment: "settings",
    owns: ["environments", "domains", "managed-services"],
  },
];

/**
 * What each consolidated tab holds, one section at a time by `?section=`:
 *
 *   - Configuration: Build (source, image, brief; the former Build pillar),
 *     Run mode & triggers (the run-spec editor and its trigger bindings; the
 *     former Control pillar, which edits how the agent runs and is not
 *     guardrails), the config editor, the manifest, CI / CD webhooks, and
 *     Model access (managed model or API key, and the live session toggle,
 *     formerly a card on Settings: it configures how this agent reaches its
 *     model, so it sits with the rest of its configuration).
 *   - Skills & tools: the agent's bound skills, and the tools they carry.
 *   - Logs & metrics: Live runs (the former Observe pillar), Metrics (the
 *     former observability page), and Deploy history (its deployments).
 *   - Access: Members, Deploy tokens, Security scans, and Guardrails (the
 *     former Secure pillar, the Zentinelle gate).
 *   - Settings: General, Environments, Domains, Managed services and the one
 *     Danger zone (SettingsPage in single-section mode).
 */
export const AGENT_TAB_SECTIONS: Partial<Record<AgentTabKey, DetailTabSection[]>> = {
  configuration: sectionsBy(
    ["build", "Build"],
    ["run-mode", "Run mode & triggers"],
    ["config", "Config editor"],
    ["manifest", "Manifest"],
    ["webhooks", "CI / CD webhooks"],
    ["model-access", "Model access"]
  ),
  skills: sectionsBy(["skills", "Skills"], ["tools", "Tools"]),
  logs: sectionsBy(
    ["live", "Live runs"],
    ["metrics", "Metrics"],
    ["deployments", "Deploy history"]
  ),
  access: sectionsBy(
    ["members", "Members"],
    ["tokens", "Deploy tokens"],
    ["security", "Security scans"],
    ["guardrails", "Guardrails"]
  ),
  settings: sectionsBy(
    ["general", "General"],
    ["environments", "Environments"],
    ["domains", "Domains"],
    ["managed-services", "Managed services"],
    ["danger-zone", "Danger zone"]
  ),
};

/**
 * Every former route under `/agents/[agentSlug]`, and where it lands now.
 * `/settings` is still the Settings tab; the rest redirect.
 */
export const AGENT_FORMER_ROUTES: Record<string, { tab: AgentTabKey; section: string | null }> = {
  overview: { tab: "overview", section: null },
  run: { tab: "runs", section: null },
  build: { tab: "configuration", section: "build" },
  control: { tab: "configuration", section: "run-mode" },
  config: { tab: "configuration", section: "config" },
  manifest: { tab: "configuration", section: "manifest" },
  webhooks: { tab: "configuration", section: "webhooks" },
  observe: { tab: "logs", section: "live" },
  observability: { tab: "logs", section: "metrics" },
  deployments: { tab: "logs", section: "deployments" },
  members: { tab: "access", section: "members" },
  tokens: { tab: "access", section: "tokens" },
  security: { tab: "access", section: "security" },
  secure: { tab: "access", section: "guardrails" },
  environments: { tab: "settings", section: "environments" },
  domains: { tab: "settings", section: "domains" },
  "managed-services": { tab: "settings", section: "managed-services" },
};

/** The slug as it sits in a pathname (`usePathname` is encoded). */
const enc = (slug: string) => encodeURIComponent(slug);

/** `/agents/<slug>` or `/agents/<slug>/<segment>`. */
export function agentTabHref(slug: string, tab: AgentTabKey): string {
  return detailTabHref(AGENT_TABS, AGENT_BASE, enc(slug), tab);
}

/** The tab that owns a pathname; Overview is the default. */
export function resolveAgentTab(pathname: string, slug: string): AgentTabKey {
  return resolveDetailTab(AGENT_TABS, AGENT_BASE, pathname, enc(slug));
}

/** The row as `DetailTab`s. */
export function agentTabs(
  slug: string,
  pathname: string,
  label?: (key: AgentTabKey) => string
): DetailTab[] {
  const tabs = label ? AGENT_TABS.map((tab) => ({ ...tab, label: label(tab.key) })) : AGENT_TABS;
  return detailTabs(tabs, AGENT_BASE, enc(slug), pathname, (label) => label);
}

/** The section a tab's query selects, or its default. */
export function activeAgentSection(tab: AgentTabKey, params: SearchParams): string {
  return activeTabSection(AGENT_TAB_SECTIONS[tab], params);
}

/** A section's href by id; the tab's own route for an unknown id. */
export function agentSectionHref(slug: string, tab: AgentTabKey, sectionId: string): string {
  const section = AGENT_TAB_SECTIONS[tab]?.find((s) => s.id === sectionId);
  return section
    ? tabSectionHref(AGENT_TABS, AGENT_BASE, enc(slug), tab, section)
    : agentTabHref(slug, tab);
}

/** Where a former route lands, keeping whatever query the old link carried. */
export function agentRedirectTarget(route: string, slug: string, params: SearchParams): string {
  const target = AGENT_FORMER_ROUTES[route] ?? { tab: "overview", section: null };
  return detailRedirectTarget(
    AGENT_TABS,
    AGENT_TAB_SECTIONS,
    AGENT_BASE,
    enc(slug),
    target.tab,
    target.section,
    params
  );
}

export type { SearchParams };
