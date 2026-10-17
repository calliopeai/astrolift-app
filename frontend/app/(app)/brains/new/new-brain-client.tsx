"use client";

import { NewBrainWizard } from "@/components/screens/brains/new/NewBrainWizard";
import { useNewBrain } from "@/components/screens/brains/new/use-new-brain";

export function NewBrainClient() {
  return <NewBrainWizard {...useNewBrain()} />;
}
