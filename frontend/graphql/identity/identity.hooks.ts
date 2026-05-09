"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { setActiveOrgGuid } from "@/lib/identity/active-org";
import { LIST_ORGANIZATIONS } from "./identity.queries";
import type { AstroliftOrganization } from "./identity.types";

interface OrganizationsResp {
  astroliftOrganizations: AstroliftOrganization[];
}

/**
 * The single organization for this install.
 *
 * Astrolift is single-tenant per install — one Organization row, one
 * tenancy boundary. The (app) layout primes LIST_ORGANIZATIONS via
 * <PreloadQuery>, so this hook is a cache read on every mount.
 *
 * Side-effect: pins the org's GUID in the `astrolift_active_org`
 * cookie so the X-Astrolift-Organization header (lib/apollo/links.ts)
 * carries it on every GraphQL request. The header is kept even though
 * single-membership inference would resolve the same org server-side
 * — it's the seam we'd reactivate to support multi-org per install if
 * a future customer asks. See #269.
 */
export function useActiveOrg() {
  const { data, loading, error } = useQuery<OrganizationsResp>(
    LIST_ORGANIZATIONS,
    { fetchPolicy: "cache-first" },
  );
  const org = data?.astroliftOrganizations[0] ?? null;

  React.useEffect(() => {
    if (org) setActiveOrgGuid(org.id);
  }, [org]);

  return { org, loading, error };
}
