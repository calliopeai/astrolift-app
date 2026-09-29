"use client";

import { useQuery } from "@apollo/client/react";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { type Principal, type ScopeRef, scopePath } from "@/components/access/access-model";
import type { BindingLabel } from "@/components/access/AccessExplainer";
import type { PrincipalSearch } from "@/components/access/GrantAccessFlow";
import { useAccessCompare, useAccessExplainer } from "@/components/access/use-access-explainer";
import { useScopeTree } from "@/components/access/use-scope-tree";
import type { SearchableUsersQuery } from "@/graphql/__generated__/operations";
import { SEARCHABLE_USERS } from "@/graphql/identity/identity.queries";
import { GET_ME } from "@/graphql/user/user.queries";
import type { CurrentUser } from "@/graphql/user/user.types";
import { useDebounce } from "@/hooks/use-debounce";

import {
  type CheckAccessQuery,
  checkAccessHref,
  parseCheckAccessQuery,
} from "./check-access-query";

interface MeResp {
  me: CurrentUser | null;
}

/**
 * People to ask about: `astroliftSearchableUsers` on the debounced text,
 * members only, because `permissionDiagnose` takes a user id and an
 * invitation has none. Runs only while something is typed.
 */
function useUserSearch(): PrincipalSearch {
  const [query, setQuery] = React.useState("");
  const term = useDebounce(query.trim(), 250);
  const { data, loading, error } = useQuery<SearchableUsersQuery>(SEARCHABLE_USERS, {
    variables: { query: term },
    skip: !term,
    fetchPolicy: "cache-first",
  });
  const results: Principal[] = term
    ? (data?.astroliftSearchableUsers ?? [])
        .filter((u) => u.matchKind === "MEMBER" && u.userId)
        .map((u) => ({
          kind: "user" as const,
          id: u.userId!,
          name: u.displayLabel || u.email,
          detail: u.email,
        }))
    : [];
  return {
    query,
    setQuery,
    results,
    loading: Boolean(term) && loading && results.length === 0,
    error: error ? { message: error.message } : null,
  };
}

/**
 * The data half of the Check access page (design 3.7): "Can <who> <do what>
 * on <which>?". The question lives in the URL (`who`, `can`, `on`, and
 * `compare` for two people; see check-access-query.ts) so an answer can be
 * linked, and who defaults to the viewer. Only the visible half queries: the
 * diagnosis while checking, the comparison while comparing, each skipped
 * until its inputs are chosen. The scope tree is the nav tree, cache-first.
 */
export function usePermissionsDiagnostics() {
  const router = useRouter();
  const params = useSearchParams();
  const qs = params?.toString() ?? "";
  const q = React.useMemo(() => parseCheckAccessQuery(new URLSearchParams(qs)), [qs]);

  const meQuery = useQuery<MeResp>(GET_ME);
  const meUser = meQuery.data?.me ?? null;
  const me: Principal | null = meUser
    ? { kind: "user", id: meUser.id, name: meUser.profile?.username || meUser.id }
    : null;

  const scopeTree = useScopeTree();
  const whoSearch = useUserSearch();
  const otherSearch = useUserSearch();

  // Names of people picked in this visit; a reloaded link names them from
  // the answer instead (the diagnosis and comparison carry usernames).
  const [known, setKnown] = React.useState<Record<string, Principal>>({});
  // "Change" clears who without falling back to the viewer.
  const [whoCleared, setWhoCleared] = React.useState(false);

  const comparing = q.compare !== null;
  const whoId = whoCleared ? null : (q.who ?? me?.id ?? null);
  const otherId = q.compare || null;

  const explainer = useAccessExplainer(comparing ? null : whoId, comparing ? null : q.can);
  const compare = useAccessCompare(comparing ? whoId : null, comparing ? otherId : null);

  function navigate(patch: Partial<CheckAccessQuery>) {
    const next = { ...q, ...patch };
    router.replace(checkAccessHref(next), { scroll: false });
  }

  function remember(p: Principal) {
    setKnown((k) => ({ ...k, [p.id]: p }));
  }

  function principalFor(id: string | null, answered?: string): Principal | null {
    if (!id) return null;
    if (me && id === me.id) return me;
    return known[id] ?? { kind: "user", id, name: answered || id };
  }

  const who = principalFor(
    whoId,
    comparing ? compare.comparison?.userAUsername : explainer.diagnosis?.username
  );
  const other = principalFor(otherId, compare.comparison?.userBUsername);

  const scope: ScopeRef | null = React.useMemo(() => {
    if (!q.on) return null;
    const path = scopePath(scopeTree.roots, q.on.kind, q.on.id);
    const node = path?.[path.length - 1];
    return { kind: q.on.kind, id: q.on.id, name: node?.name ?? q.on.id };
  }, [q.on, scopeTree.roots]);

  /**
   * Where a binding the diagnosis names can be changed: Assignments, searched
   * by its role. The label carries the scope's integer pk, which the nav
   * tree (GUIDs) cannot resolve to a page (design 6, item 7).
   */
  function bindingHref(b: BindingLabel): string {
    return `/administration/permissions/assignments?q=${encodeURIComponent(b.role)}`;
  }

  return {
    me,
    meLoading: meQuery.loading && !meQuery.data,
    comparing,
    onCompareToggle: (on: boolean) => navigate({ compare: on ? "" : null }),
    who,
    whoSearch,
    onWhoChange: (p: Principal | null) => {
      if (!p) {
        setWhoCleared(true);
        navigate({ who: null });
        return;
      }
      setWhoCleared(false);
      remember(p);
      navigate({ who: me && p.id === me.id ? null : p.id });
    },
    permission: q.can,
    onPermissionChange: (can: string) => navigate({ can }),
    scope,
    onScopeChange: (s: ScopeRef | null) => navigate({ on: s }),
    scopeTree,
    explainer,
    bindingHref,
    other,
    otherSearch,
    onOtherChange: (p: Principal | null) => {
      if (p) remember(p);
      navigate({ compare: p?.id ?? "" });
    },
    compare,
  };
}
