"use client";

import { FeaturesScreen } from "@/components/screens/administration/insights/FeaturesScreen";
import { useFeatureFlags } from "@/components/screens/administration/insights/use-feature-flags";

export function FeaturesClient() {
  return <FeaturesScreen {...useFeatureFlags()} />;
}
