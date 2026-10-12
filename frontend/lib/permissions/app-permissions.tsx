"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import type { AppViewerPermissionsQuery } from "@/graphql/__generated__/operations";
import { APP_VIEWER_PERMISSIONS } from "@/graphql/registry/registry.queries";
import { permissionMatches } from "@/lib/permissions/astrolift-permissions";
import { type PermissionCheck, useMyPermissions } from "@/lib/permissions/use-my-permissions";

const AppPermissionsContext = React.createContext<readonly string[] | null>(null);

/**
 * The app's own effective permissions for everything inside an app or agent
 * frame (#1867): an account-wide grant held on another project must not
 * offer an action this app will refuse.
 */
export function AppPermissionsProvider({
  slug,
  children,
}: {
  slug: string;
  children: React.ReactNode;
}) {
  const { data } = useQuery<AppViewerPermissionsQuery>(APP_VIEWER_PERMISSIONS, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });
  const held = data?.astroliftApp?.viewerPermissions ?? null;
  return <AppPermissionsContext.Provider value={held}>{children}</AppPermissionsContext.Provider>;
}

/**
 * Whether the viewer may do `permission` to the framing app: its own
 * permissions once they have loaded, account grants only until then
 * (optimistic while those load, as `Can` is, unless `optimistic: false`).
 * Outside a frame it is the account's answer. The backend still enforces
 * every action.
 */
export function useAppCan(permission: PermissionCheck, { optimistic = true } = {}): boolean {
  const held = React.useContext(AppPermissionsContext);
  const perms = useMyPermissions();
  if (held !== null) return permissionMatches(new Set(held), permission);
  if (perms.loading) {
    if (!optimistic) return false;
    if (perms.granted.size === 0) return true;
  }
  return perms.can(permission);
}
