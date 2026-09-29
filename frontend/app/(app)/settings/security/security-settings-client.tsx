"use client";

import { SecuritySettingsView } from "@/components/screens/settings/security/SecuritySettings";
import { useSecuritySettings } from "@/components/screens/settings/security/use-security-settings";

/** Active sessions and credential pointers. Markup lives in SecuritySettingsView. */
export function SecuritySettingsClient() {
  return <SecuritySettingsView {...useSecuritySettings()} />;
}
