"use client";

import { usePathname } from "next/navigation";

import { FEATURE_FLAG_ADMIN_PERMISSIONS, useFeatureFlag } from "@/graphql/server/server.hooks";

/** The route and flag state AdministrationSubnav renders from. */
export function useAdministrationSubnav() {
  const pathname = usePathname();

  // Permissions stays hidden until the install enables
  // `admin.permissions_enabled` (#1206), mirroring the zentinelle.enabled
  // surface gate. Until the server-info handshake resolves the flag reads
  // false, so the entry never flickers in before the answer arrives.
  const permissionsEnabled = useFeatureFlag(FEATURE_FLAG_ADMIN_PERMISSIONS);

  return { pathname, permissionsEnabled };
}
