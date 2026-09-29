import type { AstroliftRole } from "@/graphql/identity/identity.types";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";

/**
 * The catalog an editable matrix offers: every declared permission, plus any
 * slug a role already holds that the generated list does not know yet. Built
 * from the whole flat list, never from a page of roles, so a role saved from
 * the matrix cannot lose a permission the page happened not to contain.
 */
export function roleCatalog(roles: readonly AstroliftRole[]): string[] {
  const set = new Set<string>(ASTROLIFT_PERMISSIONS);
  for (const r of roles) for (const p of r.permissions) set.add(p);
  return [...set].sort((a, b) => a.localeCompare(b));
}
