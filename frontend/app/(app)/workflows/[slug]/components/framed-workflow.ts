"use client";

import * as React from "react";

import type { FramedWorkflow } from "@/components/screens/workflows/detail/use-workflow-frame";

/**
 * The workflow the frame resolved, for the tab bodies under it. The frame
 * renders its children only once the workflow is found, so inside a tab this
 * is always set.
 */
export const FramedWorkflowContext = React.createContext<FramedWorkflow | null>(null);

export function useFramedWorkflow(): FramedWorkflow {
  const value = React.useContext(FramedWorkflowContext);
  if (!value) throw new Error("useFramedWorkflow is used outside the workflow frame");
  return value;
}
