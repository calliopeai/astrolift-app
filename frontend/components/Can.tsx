"use client";

import * as React from "react";

import {
  type PermissionCheck,
  useMyPermissions,
} from "@/lib/permissions/use-my-permissions";

interface CanProps {
  permission: PermissionCheck;
  children: React.ReactNode;
  /**
   * Rendered while the viewer's permission set is still loading.
   * Default: nothing — saves an extra layout shift on the common
   * case where Apollo hits a warm cache primed by PreloadQuery.
   */
  loading?: React.ReactNode;
  /** Rendered when the permission check fails. Default: nothing. */
  fallback?: React.ReactNode;
}

/**
 * Conditional render based on the viewer's effective Astrolift
 * permissions.
 *
 *   <Can permission="app.deploy">                       <-- single permission
 *     <Button>Deploy</Button>
 *   </Can>
 *
 *   <Can permission={{ anyOf: ["app.deploy", "app.rollback"] }}>
 *     <DropdownMenu>...</DropdownMenu>
 *   </Can>
 *
 *   <Can permission={{ allOf: ["app.deploy", "secret.read"] }}>
 *     ...
 *   </Can>
 *
 * Use for action gating in tables and conditional UI inside pages.
 * For *page-level* gating, do the check in the server component
 * (see lib/permissions/server-guard.ts) so an unauthorized viewer
 * gets a real 403, not a flash of UI.
 */
export function Can({ permission, children, loading, fallback }: CanProps) {
  const { can, loading: permLoading } = useMyPermissions();
  if (permLoading) return <>{loading ?? null}</>;
  return can(permission) ? <>{children}</> : <>{fallback ?? null}</>;
}
