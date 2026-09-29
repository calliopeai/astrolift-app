/** Access presets and screen props for Home's stories and tests. */

import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";

import type { HomeScreenProps } from "./HomeScreen";
import {
  defaultLayoutFor,
  type HomeAccess,
  type HomeLayoutKey,
  offeredLayouts,
  resolveLayout,
  visiblePanels,
} from "./registry";

const ALL = new Set<string>(ASTROLIFT_PERMISSIONS);

export const ONLY_APPS: HomeAccess = { modules: new Set(["apps"]), permissions: ALL };
export const ONLY_AGENTS: HomeAccess = { modules: new Set(["agents"]), permissions: ALL };
export const BOTH: HomeAccess = { modules: new Set(["apps", "agents"]), permissions: ALL };
export const OPERATOR: HomeAccess = {
  modules: new Set(["apps", "agents", "workflows", "admin"]),
  permissions: ALL,
};
export const NO_ACCESS: HomeAccess = { modules: new Set(), permissions: new Set() };

/** What useHome returns for this access and saved layout. */
export function homeProps(
  access: HomeAccess,
  saved: HomeLayoutKey | null = null
): Omit<HomeScreenProps, "onLayoutChange"> {
  const layout = resolveLayout(saved, access);
  return {
    layout,
    layouts: offeredLayouts(access),
    panels: layout ? visiblePanels(layout, access) : [],
    defaultLayout: defaultLayoutFor(access),
  };
}
