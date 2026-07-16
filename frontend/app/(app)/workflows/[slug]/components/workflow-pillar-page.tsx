"use client";

import { StageBuilder } from "@/components/workflows/StageBuilder";

import { ObserveContent } from "./observe-content";
import { RunContent } from "./run-content";
import { WorkflowDetailShell } from "./workflow-detail-shell";

export type WorkflowPillar = "build" | "run" | "observe";

/**
 * Single client entry for every `/workflows/[slug]/<pillar>` route. The
 * server `page.tsx` resolves the slug and names its pillar; this renders the
 * shared `WorkflowDetailShell` (header + `WorkflowTabs`) and slots the
 * matching content.
 *
 * Build mounts the SAME `StageBuilder` the `/workflows/[slug]/builder`
 * route renders (owned by the builder surface — imported, never edited
 * here). `[slug]` on this route is a tier-2 Workflow slug; the shell's
 * workflow query carries `definitionSlug`, so Build resolves
 * workflow → definition and mounts the builder on the definition.
 */
export function WorkflowPillarPage({ slug, pillar }: { slug: string; pillar: WorkflowPillar }) {
  return (
    <WorkflowDetailShell workflowSlug={slug}>
      {({ workflow, refetch }) => {
        switch (pillar) {
          case "build":
            return <StageBuilder slug={workflow.definitionSlug} />;
          case "run":
            return <RunContent workflow={workflow} refetch={refetch} />;
          case "observe":
            return <ObserveContent workflow={workflow} />;
          default:
            return null;
        }
      }}
    </WorkflowDetailShell>
  );
}
