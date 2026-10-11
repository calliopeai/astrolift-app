"use client";

import { useSettingsSection } from "@/components/settings/use-settings-section";
import { permissionMatches } from "@/lib/permissions/astrolift-permissions";
import { type PermissionCheck, useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { AppSettingsTabAccess } from "./AppSettingsTab";
import { useAppSettings } from "./use-app-settings";

/**
 * The Settings tab's data: the app, the section in `?section=`, and what
 * the viewer may change (optimistic while permissions load, as `Can` is;
 * the backend still enforces each one). Each slot owns its own hook.
 */
export function useAppSettingsTab(slug: string) {
  const settings = useAppSettings(slug);
  const section = useSettingsSection();
  const perms = useMyPermissions();
  // The app's own effective permissions once it has loaded (#1867): an
  // account-wide grant held on some other project must not offer an action
  // this app will refuse. Account grants only bridge the first load.
  const scoped = settings.app?.viewerPermissions;
  const allow = (p: PermissionCheck) =>
    scoped
      ? permissionMatches(new Set(scoped), p)
      : (perms.loading && perms.granted.size === 0) || perms.can(p);
  const access: AppSettingsTabAccess = {
    update: allow("app.update"),
    deploy: allow("app.deploy"),
    delete: allow("app.delete"),
    webhooks: allow("webhook.update"),
  };
  return { ...settings, section, access };
}
