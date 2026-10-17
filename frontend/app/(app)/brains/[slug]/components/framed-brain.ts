"use client";

import * as React from "react";

import type { FramedBrainValue } from "@/components/screens/brains/detail/use-brain-frame";

/**
 * The layer the frame resolved, for the tab bodies under it. The frame
 * renders its children only once the layer is found, so inside a tab this
 * is always set.
 */
export const FramedBrainContext = React.createContext<FramedBrainValue | null>(null);

export function useFramedBrain(): FramedBrainValue {
  const value = React.useContext(FramedBrainContext);
  if (!value) throw new Error("useFramedBrain is used outside the semantic layer frame");
  return value;
}
