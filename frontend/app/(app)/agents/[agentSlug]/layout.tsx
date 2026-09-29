import type * as React from "react";

import { AgentFrameContainer } from "./components/agent-frame";

/**
 * Every agent page sits in one frame (spec 44 §5.2): `Agents ▾ › <agent>`,
 * the title row with Run now, and the one row of tabs. The frame stays
 * mounted as the tabs change, so only the body re-renders.
 */
export default async function AgentDetailLayout({
  params,
  children,
}: {
  params: Promise<{ agentSlug: string }>;
  children: React.ReactNode;
}) {
  const { agentSlug } = await params;
  return <AgentFrameContainer agentSlug={agentSlug}>{children}</AgentFrameContainer>;
}
