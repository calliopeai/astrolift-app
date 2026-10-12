"use client";

import { useSettingsSection } from "@/components/settings/use-settings-section";
import { type PermissionCheck, useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { useAppCan } from "@/lib/permissions/app-permissions";

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
  // Members is the organization's; the rest are this app's (#1867).
  const tokens = useAppCan("app.deploy");
  const security = useAppCan("app.update");
  const edge = useAppCan("app.access");
  const access: AppAccessAccess = { members: allow("org.manage_members"), tokens, security, edge };
  return { section, access };
}
