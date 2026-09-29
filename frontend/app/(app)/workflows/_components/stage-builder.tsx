"use client";

import { StageBuilder } from "@/components/workflows/StageBuilder";
import { useStageBuilder } from "@/components/workflows/use-stage-builder";

/** The stage builder wired to one definition. */
export function StageBuilderContainer({ slug }: { slug: string }) {
  return <StageBuilder {...useStageBuilder(slug)} />;
}
