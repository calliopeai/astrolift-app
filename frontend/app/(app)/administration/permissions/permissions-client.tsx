"use client";

import type * as React from "react";

import type { PermissionsPage } from "@/components/screens/administration/access/admin-crumbs";
import { PermissionsScreen } from "@/components/screens/administration/permissions/PermissionsScreen";
import { FEATURE_FLAG_ADMIN_PERMISSIONS, useFeatureFlag } from "@/graphql/server/server.hooks";

/**
 * The `admin.permissions_enabled` gate shared by Roles, Assignments and
 * Diagnostics. The page is mounted only while the flag is on, so its queries
 * never fire while gated.
 */
export function PermissionsClient({
  page,
  children,
}: {
  page: PermissionsPage;
  children: React.ReactNode;
}) {
  const enabled = useFeatureFlag(FEATURE_FLAG_ADMIN_PERMISSIONS);
  return (
    <PermissionsScreen enabled={enabled} page={page}>
      {children}
    </PermissionsScreen>
  );
}
