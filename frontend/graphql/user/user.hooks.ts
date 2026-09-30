"use client";

import * as React from "react";

import { useQuery } from "@apollo/client/react";
import { GET_ME } from "./user.queries";
import type { MeQueryData, MeQueryVariables, ModuleEntitlement } from "./user.types";

export const useMe = () => {
  const { data, loading, error } = useQuery<MeQueryData, MeQueryVariables>(GET_ME, {
    fetchPolicy: "cache-first",
  });

  return {
    data,
    user: data?.me ?? null,
    loading,
    error,
  };
};

// The entitlement-driven module keys the shell knows about (spec 36
// §1.2). `dashboard` is intentionally absent — it is always rendered
// and carries no `me.modules` entry.
export type ModuleKey = "apps" | "agents" | "workflows" | "admin";

// All-false capability for a module the server didn't return (and for
// anonymous / no-tenant viewers, where `me.modules` is `[]`). Frozen so
// the same object is reused for every miss.
const NO_CAPS: Readonly<Omit<ModuleEntitlement, "key">> = Object.freeze({
  canView: false,
  canCreate: false,
  canManage: false,
  canRun: false,
  enabled: false,
});

/**
 * Server-authoritative module capability manifest for the signed-in
 * viewer (spec 34 §3.2.3 — the frontend renders the server's answer,
 * it carries no permission *logic*).
 *
 * Reads the same SSR-primed `GET_ME` cache entry as {@link useMe}; the
 * (app) layout primes it via `client.query({ query: GET_ME })` so the
 * first render already has the data. `cache-and-network` (not
 * cache-first) so a stale or 401-poisoned SSR result self-heals from
 * the client request that carries the user's cookies — matching
 * `useMyPermissions` / `useActiveOrg`.
 *
 * Anonymous / no-tenant viewers get `modules: []` from the backend, so
 * every helper returns `false`.
 */
export function useModules() {
  const { data, loading, error, refetch } = useQuery<MeQueryData, MeQueryVariables>(GET_ME, {
    fetchPolicy: "cache-and-network",
  });

  // Keyed map for O(1) lookup; rebuilt only when the module list
  // identity changes.
  const byKey = React.useMemo(() => {
    const map = new Map<string, ModuleEntitlement>();
    for (const m of data?.me?.modules ?? []) {
      map.set(m.key, m);
    }
    return map;
  }, [data?.me?.modules]);

  const caps = React.useCallback((key: ModuleKey) => byKey.get(key) ?? NO_CAPS, [byKey]);

  return {
    loading,
    error,
    refetch,
    hasData: Boolean(data),
    /** Keyed entitlement map; missing keys are simply absent. */
    modules: byKey,
    /** True when the viewer may see the module's surface. */
    canView: (key: ModuleKey) => caps(key).canView,
    /** True when the viewer may create the module's primary entity. */
    canCreate: (key: ModuleKey) => caps(key).canCreate,
    /** True when the viewer may edit/delete existing entities. */
    canManage: (key: ModuleKey) => caps(key).canManage,
    /** True when the viewer may run/dispatch/trigger the module. */
    canRun: (key: ModuleKey) => caps(key).canRun,
  };
}
