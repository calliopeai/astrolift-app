"use client";

import { BoxesTabView } from "@/components/screens/agents/list/BoxesTabView";
import { useAgentBoxes } from "@/components/screens/agents/list/use-agent-boxes";

/** Boxes tab container: runs the boxes hook and renders the view. */
export function BoxesTab({ orgId }: { orgId: string }) {
  return <BoxesTabView {...useAgentBoxes(orgId)} />;
}
