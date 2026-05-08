"use client";

import { useQuery } from "@apollo/client/react";

import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";

import {
  type AstroliftPermission,
  type PermissionCheck,
  permissionMatches,
} from "./astrolift-permissions";

interface MyPermissionsResp {
  astroliftMyPermissions: string[];
}

const EMPTY = new Set<string>();

/**
 * Effective Astrolift permission slugs for the signed-in viewer.
 *
 * Cached at the Apollo level — every component that calls `can(...)`
 * hits the same cache entry. The (app) layout primes it via
 * PreloadQuery so the first render already has the data.
 */
export function useMyPermissions() {
  const { data, loading, error } = useQuery<MyPermissionsResp>(
    GET_MY_PERMISSIONS,
    { fetchPolicy: "cache-first" },
  );

  const granted: ReadonlySet<string> = data?.astroliftMyPermissions
    ? new Set(data.astroliftMyPermissions)
    : EMPTY;

  return {
    loading,
    error,
    granted,
    /** Returns true when the viewer has the permission(s) requested. */
    can: (check: PermissionCheck) => permissionMatches(granted, check),
    /** Returns true when the viewer is missing the permission(s) requested. */
    cannot: (check: PermissionCheck) => !permissionMatches(granted, check),
    /** True when at least one permission is granted (proxy for "has any role"). */
    hasAnyAccess: granted.size > 0,
  };
}

export type { AstroliftPermission, PermissionCheck };
