"use client";

import { BrainSharingTab } from "@/components/screens/brains/detail/BrainSharingTab";
import { useBrainSharing } from "@/components/screens/brains/detail/use-brain-sharing";

import { useFramedBrain } from "./framed-brain";

export function SharingContent() {
  return <BrainSharingTab {...useBrainSharing(useFramedBrain())} />;
}
