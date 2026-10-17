"use client";

import { BrainSubstrateTab } from "@/components/screens/brains/detail/BrainSubstrateTab";
import { useBrainSubstrate } from "@/components/screens/brains/detail/use-brain-substrate";

import { useFramedBrain } from "./framed-brain";

export function SubstrateContent() {
  return <BrainSubstrateTab {...useBrainSubstrate(useFramedBrain())} />;
}
