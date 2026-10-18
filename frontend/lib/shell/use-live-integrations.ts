"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import {
  hasAnyAccess,
  zentinelleAccess,
  zentinelleInNav,
} from "@/components/screens/secure/zentinelle-model";
import type { ZentinelleCapabilityQuery } from "@/graphql/__generated__/operations";
import { ZENTINELLE_CAPABILITY } from "@/graphql/zentinelle/zentinelle.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import type { NavRequirement } from "@/lib/shell/nav-model";

/**
 * The integrations live for the viewer, for the rows that `requires` one.
 * Zentinelle reads the one-boolean capability query (Astrolift's own record,
 * no call out), only for a viewer with a zentinelle.* grant; the screen's
 * writes refetch it, so connecting or disconnecting updates the rail.
 */
export function useLiveIntegrations(): ReadonlySet<NavRequirement> {
  const { can } = useMyPermissions();
  const access = zentinelleAccess(can);
  const anyAccess = hasAnyAccess(access);
  const { data } = useQuery<ZentinelleCapabilityQuery>(ZENTINELLE_CAPABILITY, {
    fetchPolicy: "cache-first",
    skip: !anyAccess,
  });
  const live = zentinelleInNav(access, Boolean(data?.astroliftZentinelleCapability.connected));
  return React.useMemo(() => new Set<NavRequirement>(live ? ["zentinelle"] : []), [live]);
}
