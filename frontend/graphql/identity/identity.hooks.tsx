"use client";

import { useQuery } from "@apollo/client/react";
import type { ErrorLike } from "@apollo/client";
import * as React from "react";

import { getActiveOrgGuid, setActiveOrgGuid } from "@/lib/identity/active-org";
import { LIST_ORGANIZATIONS } from "./identity.queries";
import type { AstroliftOrganization } from "./identity.types";

interface OrganizationsResp {
  astroliftOrganizations: AstroliftOrganization[];
}

interface ActiveOrgContextValue {
  org: AstroliftOrganization | null;
  loading: boolean;
  error: ErrorLike | undefined;
}

const ActiveOrgContext = React.createContext<ActiveOrgContextValue | null>(null);

/**
 * Resolves the single organization for this install and provides it to
 * the whole (app) tree. Mount once in the (app) layout.
 *
 * Astrolift is single-tenant per install — one Organization row, one
 * tenancy boundary. The (app) layout primes LIST_ORGANIZATIONS
 * server-side; cache-and-network here paints from that cache and then
 * confirms against the live backend (the SSR result can be empty when
 * the server-side fetch failed), a single fetch per app load instead of
 * one per consumer mount.
 *
 * Side-effect: pins the org's GUID in the `astrolift_active_org` cookie
 * so the X-Astrolift-Organization header (lib/apollo/links.ts) carries
 * it on every GraphQL request. The header is kept even though
 * single-membership inference would resolve the same org server-side —
 * it's the seam we'd reactivate to support multi-org per install if a
 * future customer asks. See #269.
 *
 * #1022 — the cookie is written during render, not in an effect. Child
 * effects run before parent effects, so an effect here would fire after
 * the org-scoped queries that un-skip on this same render have already
 * left without the header. Writing before children render closes that
 * race; the write is idempotent and guarded, so re-renders are free.
 */
export function ActiveOrgProvider({ children }: { children: React.ReactNode }) {
  const { data, loading, error } = useQuery<OrganizationsResp>(LIST_ORGANIZATIONS, {
    fetchPolicy: "cache-and-network",
  });
  const org = data?.astroliftOrganizations[0] ?? null;

  if (org && getActiveOrgGuid() !== org.id) setActiveOrgGuid(org.id);

  const value = React.useMemo(() => ({ org, loading, error }), [org, loading, error]);

  return <ActiveOrgContext.Provider value={value}>{children}</ActiveOrgContext.Provider>;
}

/**
 * The active organization, resolved once by ActiveOrgProvider.
 *
 * Reactive: consumers re-render when the org loads, so `skip: !orgId`
 * queries fire as soon as it's available — never read the cookie
 * synchronously at render time instead (#1022, it races the setter).
 */
export function useActiveOrg(): ActiveOrgContextValue {
  const ctx = React.useContext(ActiveOrgContext);
  if (!ctx) {
    throw new Error("useActiveOrg must be used inside <ActiveOrgProvider>");
  }
  return ctx;
}
