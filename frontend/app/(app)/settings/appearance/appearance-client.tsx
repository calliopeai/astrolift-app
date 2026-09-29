"use client";

import { AppearanceSettings } from "@/components/screens/settings/account/AppearanceSettings";
import { useAppearanceSettings } from "@/components/screens/settings/account/use-appearance-settings";

export function AppearanceClient() {
  return <AppearanceSettings {...useAppearanceSettings()} />;
}
