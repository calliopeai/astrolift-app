"use client";

import { usePathname } from "next/navigation";

import { WorkflowTabsView } from "@/components/screens/workflows/detail/WorkflowTabs";

interface WorkflowTabsProps {
  workflowSlug: string;
}

/**
 * The per-workflow BROCS pillar bar, wired to the pathname. The view lives in
 * components/screens/workflows/detail/WorkflowTabs.
 */
export function WorkflowTabs({ workflowSlug }: WorkflowTabsProps) {
  const pathname = usePathname() ?? "";
  return <WorkflowTabsView workflowSlug={workflowSlug} pathname={pathname} />;
}
