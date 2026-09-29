import type * as React from "react";

import { WorkflowFrameContainer } from "./components/workflow-frame";

/**
 * Every workflow page sits in one frame (spec 44 §5.2): `Agents ▾ ›
 * Workflows › <workflow>`, the title row with Run, and the one row of tabs.
 * The frame stays mounted as the tabs change, so only the body re-renders.
 */
export default async function WorkflowDetailLayout({
  params,
  children,
}: {
  params: Promise<{ slug: string }>;
  children: React.ReactNode;
}) {
  const { slug } = await params;
  return <WorkflowFrameContainer slug={slug}>{children}</WorkflowFrameContainer>;
}
