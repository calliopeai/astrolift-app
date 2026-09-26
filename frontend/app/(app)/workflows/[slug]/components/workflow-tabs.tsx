"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/**
 * Per-workflow BROCS pillar nav — the workflow-detail clone of `AgentTabs`
 * (`app/(app)/agents/[agentSlug]/components/agent-tabs.tsx`).
 *
 * Same mechanism: a primary pillar bar over flat nested routes at
 * `/workflows/[slug]/<pillar>`, with the active pillar resolved from the
 * pathname. One route per pillar, so the pillar bar is the whole nav.
 *
 * Pillars (spec 40 / #971):
 *   Build   — the stage builder (shares content with `/workflows/[slug]/builder`)
 *   Run     — Run-now + trigger configuration + latest run status
 *   Observe — run history with per-run engine timeline drill-down
 */

type PillarKey = "build" | "run" | "observe";

interface Pillar {
  key: PillarKey;
  label: string;
  /** Path suffix under `/workflows/[slug]/`. */
  segment: string;
}

const PILLARS: readonly Pillar[] = [
  { key: "build", label: "Build", segment: "build" },
  { key: "run", label: "Run", segment: "run" },
  { key: "observe", label: "Observe", segment: "observe" },
];

const at = (workflowSlug: string, segment: string) =>
  `/workflows/${encodeURIComponent(workflowSlug)}/${segment}`;

/** A pillar owns the pathname when the URL is its route or a child of it. */
function isActive(pathname: string, workflowSlug: string, segment: string): boolean {
  const base = at(workflowSlug, segment);
  return pathname === base || pathname.startsWith(`${base}/`);
}

/** Resolve the active pillar from the pathname, defaulting to Build (the
 * landing tab). */
function resolveActivePillar(pathname: string, workflowSlug: string): PillarKey {
  for (const pillar of PILLARS) {
    if (isActive(pathname, workflowSlug, pillar.segment)) return pillar.key;
  }
  return "build";
}

interface WorkflowTabsProps {
  workflowSlug: string;
}

export function WorkflowTabs({ workflowSlug }: WorkflowTabsProps) {
  const pathname = usePathname() ?? "";
  const activePillar = resolveActivePillar(pathname, workflowSlug);

  return (
    <div className="-mx-6">
      <nav
        aria-label="Workflow pillars"
        className="border-border flex gap-1 overflow-x-auto border-b px-6 scrollbar-none [mask-image:linear-gradient(to_right,transparent_0,black_1.5rem,black_calc(100%-3rem),transparent_100%)]"
      >
        {PILLARS.map((pillar) => {
          const active = pillar.key === activePillar;
          return (
            <Link
              key={pillar.key}
              href={at(workflowSlug, pillar.segment)}
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
