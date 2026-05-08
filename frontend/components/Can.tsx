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
   * Default: the children — optimistic render avoids a flash of
   * "no buttons" on first paint. The backend still enforces the
   * permission, so the worst case is a click that toasts a
   * PERMISSION_DENIED. Pass `null` here for a strict-fail variant.
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
  const { can, granted, loading: permLoading } = useMyPermissions();
  // While the permissions query is still flying AND we don't have a
  // cached set yet, render the children optimistically. Once we have
  // any data (granted.size > 0 or a confirmed empty set after a
  // resolved fetch), gate normally.
  if (permLoading && granted.size === 0) {
    return <>{loading ?? children}</>;
  }
  return can(permission) ? <>{children}</> : <>{fallback ?? null}</>;
}
