"use client";

import * as React from "react";

import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

export interface FramedAgent {
  agent: AstroliftAgentListItem;
  orgId: string;
}

/**
 * The agent the frame resolved, for the tab bodies under it. The frame
 * renders its children only once the agent is found, so inside a tab this
 * is always set.
 */
export const FramedAgentContext = React.createContext<FramedAgent | null>(null);

export function useFramedAgent(): FramedAgent {
  const value = React.useContext(FramedAgentContext);
  if (!value) throw new Error("useFramedAgent is used outside the agent frame");
  return value;
}
