import { type PermissionCheck, permissionMatches } from "./astrolift-permissions";

/**
 * Whether the viewer may do `permission` to one environment, from its own
 * `viewerPermissions` (#1867): the server answers per environment, so a
 * policy that denies deploys to production alone, or a grant on another
 * project, offers nothing here the mutation would refuse. The backend still
 * enforces every action.
 */
export function environmentCan(
  env: { viewerPermissions?: readonly string[] | null } | null | undefined,
  permission: PermissionCheck
): boolean {
  return permissionMatches(new Set(env?.viewerPermissions ?? []), permission);
}
