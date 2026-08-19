/**
 * Permission checks the UI gates on.
 *
 * The slug list itself lives in `./permissions.generated`, rendered from
 * `core/permissions.py:Permission` by `make perms`. It used to be a
 * hand-maintained union here and had fallen 29 slugs behind the backend
 * (#1490) — every `agent.*`, `workflow.*` and `form.*` grant was
 * unreferenceable from the UI because writing one was a type error.
 *
 * The backend resolver stays the authority on the yes/no; this side only
 * decides what to render.
 */

import { ASTROLIFT_PERMISSIONS, type AstroliftPermission } from "./permissions.generated";

export { ASTROLIFT_PERMISSIONS };
export type { AstroliftPermission };

export type PermissionCheck =
  | AstroliftPermission
  | { anyOf: AstroliftPermission[] }
  | { allOf: AstroliftPermission[] };

export function permissionMatches(granted: ReadonlySet<string>, check: PermissionCheck): boolean {
  if (typeof check === "string") return granted.has(check);
  if ("anyOf" in check) return check.anyOf.some((p) => granted.has(p));
  if ("allOf" in check) return check.allOf.every((p) => granted.has(p));
  return false;
}
