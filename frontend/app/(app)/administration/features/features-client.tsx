"use client";

import { FeaturesScreen } from "@/components/screens/administration/insights/FeaturesScreen";
import { useFeatureFlags } from "@/components/screens/administration/insights/use-feature-flags";
import { useSettingsSection } from "@/components/settings/use-settings-section";

/** Runtime flags or install-time features, one at a time (`?section=`). */
export function FeaturesClient() {
  return <FeaturesScreen {...useFeatureFlags()} section={useSettingsSection()} />;
}
