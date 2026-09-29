"use client";

import { useSettingsSection } from "@/components/settings/use-settings-section";
import { type PermissionCheck, useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { AppAccessAccess } from "./AppAccessScreen";

/**
 * The Access tab's section (`?section=`) and what the viewer may change in
 * each one: optimistic while permissions load, as `Can` is. Each section's
 * own client fetches its data.
 */
export function useAppAccessTab() {
  const section = useSettingsSection();
  const perms = useMyPermissions();
  const allow = (p: PermissionCheck) => (perms.loading && perms.granted.size === 0) || perms.can(p);
  const access: AppAccessAccess = {
    members: allow("org.manage_members"),
    tokens: allow("app.deploy"),
    security: allow("app.update"),
    edge: allow("app.access"),
  };
  return { section, access };
}
