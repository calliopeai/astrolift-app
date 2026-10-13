"use client";

import { PlatformSettingsScreen } from "@/components/screens/administration/insights/PlatformSettingsScreen";
import { usePlatformSecretStores } from "@/components/screens/administration/insights/use-platform-secret-stores";
import { usePlatformSettings } from "@/components/screens/administration/insights/use-platform-settings";
import { useSettingsSection } from "@/components/settings/use-settings-section";

/** Install settings, one group at a time (`?section=`) (#1861). */
export function PlatformSettingsClient() {
  return (
    <PlatformSettingsScreen
      {...usePlatformSettings()}
      secrets={usePlatformSecretStores()}
      section={useSettingsSection()}
    />
  );
}
