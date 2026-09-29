"use client";

import { RunsScreen } from "@/components/screens/tasks/RunsScreen";
import { useRuns } from "@/components/screens/tasks/use-runs";

import { useFramedAgent } from "./framed-agent";

/**
 * The Runs tab: the Runs list embedded, narrowed to this agent (spec 44
 * §5.2). Run now is the frame's title-row action.
 */
export function RunContent() {
  const { agent } = useFramedAgent();
  return <RunsScreen {...useRuns({ agent: { id: agent.id, slug: agent.slug } })} />;
}
