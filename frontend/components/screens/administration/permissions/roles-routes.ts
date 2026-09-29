import type { DetailTab } from "@/components/DetailPageTabs";
import {
  type DetailTabSpec,
  detailTabHref,
  detailTabs,
} from "@/components/detail/detail-tabs-model";
import type { Crumb } from "@/components/shell/ShellHeader";

import { permissionsCrumbs } from "../access/admin-crumbs";

/**
 * Where roles live (design 3.5): the list at /administration/permissions, a
 * role's page at `/roles/<id>` with one route per tab (spec 44 §5.2), and the
 * stepped create page at `/roles/new?from=<id>`. Tabs are routes, not
 * `?tab=`, because the Holders list keeps its own search and cursor in the
 * query string and would otherwise wipe the tab. Pure.
 */
export const ROLES_HREF = "/administration/permissions";
export const ROLE_BASE = "/administration/permissions/roles";

export type RoleTab = "permissions" | "holders" | "settings";

export const ROLE_TABS: readonly DetailTabSpec<RoleTab>[] = [
  { key: "permissions", label: "Permissions", segment: "", owns: [] },
  { key: "holders", label: "Holders", segment: "holders", owns: [] },
  { key: "settings", label: "Settings", segment: "settings", owns: [] },
];

export function roleHref(id: string, tab: RoleTab = "permissions"): string {
  return detailTabHref(ROLE_TABS, ROLE_BASE, encodeURIComponent(id), tab);
}

/** The create page, seeded from `from` when duplicating. */
export function newRoleHref(from?: string): string {
  return from ? `${ROLE_BASE}/new?from=${encodeURIComponent(from)}` : `${ROLE_BASE}/new`;
}

export function roleTabs(id: string, active: RoleTab): DetailTab[] {
  const slug = encodeURIComponent(id);
  return detailTabs(ROLE_TABS, ROLE_BASE, slug, roleHref(id, active), (label) => label, active);
}

/** `Admin ▾ › Permissions ▾ › Roles › <role>`: four crumbs, the most spec 44 §4.4 allows. */
export function roleCrumbs(name: string): Crumb[] {
  const [admin, permissions] = permissionsCrumbs("roles");
  return [admin, permissions, { label: "Roles", href: ROLES_HREF }, { label: name }];
}

/**
 * The page of an IdP group, where its mappings are managed (design 3.2).
 * The People workflow owns that route; this is the shape the design names
 * (`/administration/access/people/[id]`, user or group).
 */
export function groupHref(externalId: string): string {
  return `/administration/access/people/${encodeURIComponent(`group:${externalId}`)}`;
}

/** The Grant access flow (design 3.4), preselected to a role and returning here. */
export function grantRoleHref(roleId: string): string {
  const q = new URLSearchParams({ role: roleId, return: roleHref(roleId, "holders") });
  return `/administration/access/grant?${q.toString()}`;
}
