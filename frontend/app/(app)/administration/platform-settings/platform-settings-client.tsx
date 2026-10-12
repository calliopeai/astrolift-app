"use client";

import { PlatformSettingsScreen } from "@/components/screens/administration/insights/PlatformSettingsScreen";
import { usePlatformSettings } from "@/components/screens/administration/insights/use-platform-settings";
import { useSettingsSection } from "@/components/settings/use-settings-section";

/** Install settings, one group at a time (`?section=`) (#1861). */
export function PlatformSettingsClient() {
  return <PlatformSettingsScreen {...usePlatformSettings()} section={useSettingsSection()} />;
}
