"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/**
 * Per-agent BROCS pillar nav — the agent-detail clone of `AppTabs`
 * (`app/(app)/apps/[slug]/components/app-tabs.tsx`).
 *
 * Same mechanism: a primary pillar bar over flat nested routes at
 * `/agents/[agentSlug]/<pillar>`, with the active pillar resolved from the
 * pathname. The app surface folds a dozen sub-routes into five pillars and
 * therefore renders a second-level sub-tab row; the agent surface is one
 * route per pillar, so there's no secondary row — the pillar bar is the
 * whole nav.
 *
 * Labels are inline strings (not i18n) to match the rest of the agents area
 * (`agents-client.tsx`, skills, tools), which is built without the
 * `next-intl` namespaces the apps surface uses.
 *
 * Pillar status (spec 33 PR-9):
 *   Build   — real (repo/manifest/brief/skills/tools/image)
 *   Run     — stub  (Dispatch-now + Executions land in PR-10)
 *   Observe — real (AgentTheatre + per-run logs)
 *   Control — stub  (run-spec editor lands in PR-11/12)
 *   Secure  — Zentinelle gate (deferred/enterprise placeholder)
 */

type PillarKey = "build" | "run" | "observe" | "control" | "secure";

interface Pillar {
  key: PillarKey;
  label: string;
  /** Path suffix under `/agents/[agentSlug]/`. */
  segment: string;
}

const PILLARS: readonly Pillar[] = [
  { key: "build", label: "Build", segment: "build" },
  { key: "run", label: "Run", segment: "run" },
  { key: "observe", label: "Observe", segment: "observe" },
  { key: "control", label: "Control", segment: "control" },
  { key: "secure", label: "Secure", segment: "secure" },
];

const at = (agentSlug: string, segment: string) =>
  `/agents/${encodeURIComponent(agentSlug)}/${segment}`;

/** A pillar owns the pathname when the URL is its route or a child of it. */
function isActive(pathname: string, agentSlug: string, segment: string): boolean {
  const base = at(agentSlug, segment);
  return pathname === base || pathname.startsWith(`${base}/`);
}

/** Resolve the active pillar from the pathname, defaulting to Build (the
 * landing tab — Run is a stub until PR-10). */
function resolveActivePillar(pathname: string, agentSlug: string): PillarKey {
  for (const pillar of PILLARS) {
    if (isActive(pathname, agentSlug, pillar.segment)) return pillar.key;
  }
  return "build";
}

interface AgentTabsProps {
  agentSlug: string;
}

export function AgentTabs({ agentSlug }: AgentTabsProps) {
  const pathname = usePathname() ?? "";
  const activePillar = resolveActivePillar(pathname, agentSlug);

  return (
    <div className="-mx-6">
      <nav
        aria-label="Agent pillars"
        className="border-border flex gap-1 overflow-x-auto border-b px-6 scrollbar-none [mask-image:linear-gradient(to_right,transparent_0,black_1.5rem,black_calc(100%-3rem),transparent_100%)]"
      >
        {PILLARS.map((pillar) => {
          const active = pillar.key === activePillar;
          return (
            <Link
              key={pillar.key}
              href={at(agentSlug, pillar.segment)}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative shrink-0 px-3 py-2.5 text-sm font-medium transition-colors",
                active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              {pillar.label}
              {active && (
                <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-[var(--brand-primary)]" />
              )}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
