"use client";

import { StageBuilderContainer } from "../../_components/stage-builder";
import { useFramedWorkflow } from "./framed-workflow";

/**
 * The Builder tab: the stage builder on the definition, the one `/builder`
 * mounted. A configured workflow builds on the definition behind it; the
 * builder itself shows a repository or catalogue definition read-only.
 */
export function BuilderContent() {
  const framed = useFramedWorkflow();
  const slug =
    framed.kind === "configured" ? framed.workflow.definitionSlug : framed.definition.slug;
  return <StageBuilderContainer slug={slug} />;
}
