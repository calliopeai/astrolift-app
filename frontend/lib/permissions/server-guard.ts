import { getClient } from "@/lib/apollo";
import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";

import {
  type PermissionCheck,
  permissionMatches,
} from "./astrolift-permissions";

interface MyPermissionsResp {
  astroliftMyPermissions: string[];
}

/**
 * Server-side permission check for page guards.
 *
 * Returns true when the viewer has the requested permission(s).
 * Returns false on any failure (network, unauthenticated, missing
 * permission) — the caller decides what to do (notFound() for a
 * silent 404, or a redirect to a "no access" page).
 *
 *   // In a server component
 *   if (!(await viewerHasPermission("app.deploy"))) {
 *     notFound();
 *   }
 */
export async function viewerHasPermission(
  check: PermissionCheck,
): Promise<boolean> {
  try {
    const client = await getClient();
    const { data } = await client.query<MyPermissionsResp>({
      query: GET_MY_PERMISSIONS,
      fetchPolicy: "no-cache",
    });
    const granted = new Set(data?.astroliftMyPermissions ?? []);
    return permissionMatches(granted, check);
  } catch {
    return false;
  }
}
