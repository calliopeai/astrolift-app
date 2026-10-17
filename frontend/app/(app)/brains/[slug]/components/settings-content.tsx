"use client";

import { BrainSettingsTab } from "@/components/screens/brains/detail/BrainSettingsTab";
import { useBrainSettings } from "@/components/screens/brains/detail/use-brain-settings";

import { useFramedBrain } from "./framed-brain";

export function SettingsContent() {
  return <BrainSettingsTab {...useBrainSettings(useFramedBrain())} />;
}
