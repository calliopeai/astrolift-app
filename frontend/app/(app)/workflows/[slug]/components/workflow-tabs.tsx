"use client";

import { usePathname } from "next/navigation";

import { DetailTabRow } from "@/components/DetailPageTabs";

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
    <div className="-mx-6 min-w-0">
      <DetailTabRow
        ariaLabel="Workflow pillars"
        tabs={PILLARS.map((pillar) => ({
          key: pillar.key,
          label: pillar.label,
          href: at(workflowSlug, pillar.segment),
          active: pillar.key === activePillar,
        }))}
      />
    </div>
  );
}
