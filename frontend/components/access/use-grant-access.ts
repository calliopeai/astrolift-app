"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";

import type {
  ListRolesICanGrantQuery,
  ListTeamMembersQuery,
  ListTeamsPageQuery,
  SearchableUsersQuery,
} from "@/graphql/__generated__/operations";
import { GRANT_ROLE } from "@/graphql/identity/identity.mutations";
import {
  LIST_MEMBERS,
  LIST_ROLE_BINDINGS,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_ROLES_I_CAN_GRANT,
  LIST_TEAM_MEMBERS,
  LIST_TEAMS_PAGE,
  SEARCHABLE_USERS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftRoleBinding,
  MutationResult,
  ScopeKind,
} from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";

import type { Principal, RoleRef } from "./access-model";
import type { GrantDraft, GrantOutcome, GrantPreview } from "./GrantAccessFlow";
import { useScopeTree } from "./use-scope-tree";

const SEARCH_LIMIT = 10;

/**
 * The data half of `GrantAccessFlow`, on today's API:
 *
 * - who: `astroliftSearchableUsers` (members only; an invitation cannot hold
 *   a binding) and `astroliftTeamsPage`, both on the debounced query;
 * - roles: `astroliftRolesICanGrant`, so every role offered can be granted;
 * - scope: the nav tree, cache-first (`useScopeTree`);
 * - submit: one `grantRole` per person, a team expanded to its members at
 *   the time of the grant. IdP groups cannot be granted to yet (`grantRole`
 *   takes a user), and neither can an expiry, so the flow offers Never only;
 * - preview: approximate. It lists what the role carries for each person,
 *   not what is new to them: there is no grant preview query yet.
 */
export function useGrantAccess() {
  const client = useApolloClient();
  const [query, setQuery] = React.useState("");
  const term = useDebounce(query.trim(), 250);

  const users = useQuery<SearchableUsersQuery>(SEARCHABLE_USERS, {
    variables: { query: term },
    skip: !term,
    fetchPolicy: "cache-first",
  });
  const teams = useQuery<ListTeamsPageQuery>(LIST_TEAMS_PAGE, {
    variables: { search: term, limit: SEARCH_LIMIT },
    skip: !term,
    fetchPolicy: "cache-first",
  });
  const roles = useQuery<ListRolesICanGrantQuery>(LIST_ROLES_I_CAN_GRANT, {
    fetchPolicy: "cache-and-network",
  });
  const scopeTree = useScopeTree();
  const [grantRole] = useMutation<{
    grantRole: MutationResult<AstroliftRoleBinding>;
  }>(GRANT_ROLE);

  const results: Principal[] = term
    ? [
        ...(users.data?.astroliftSearchableUsers ?? [])
          .filter((u) => u.matchKind === "MEMBER" && u.userId)
          .map((u) => ({
            kind: "user" as const,
            id: u.userId!,
            name: u.displayLabel || u.email,
            detail: u.email,
          })),
        ...(teams.data?.astroliftTeamsPage.items ?? []).map((t) => ({
          kind: "team" as const,
          id: t.id,
          name: t.name,
          detail: t.slug,
        })),
      ]
    : [];

  const roleRefs: RoleRef[] = (roles.data?.astroliftRolesICanGrant ?? []).map((r) => ({
    ...r,
    scopeLevel: r.scopeLevel as ScopeKind,
  }));

  /** Teams become their members, now; users stay; groups are refused below. */
  async function expand(principals: Principal[]): Promise<Principal[]> {
    const out: Principal[] = [];
    for (const p of principals) {
      if (p.kind !== "team") {
        out.push(p);
        continue;
      }
      const { data } = await client.query<ListTeamMembersQuery>({
        query: LIST_TEAM_MEMBERS,
        variables: { teamId: p.id },
        fetchPolicy: "network-only",
      });
      for (const m of data?.astroliftTeamMembers ?? []) {
        if (!m.isActive) continue;
        out.push({ kind: "user", id: m.user.id, name: m.user.username, detail: m.user.email });
      }
    }
    const seen = new Set<string>();
    return out.filter((p) => {
      const key = `${p.kind}:${p.id}`;
      return !seen.has(key) && Boolean(seen.add(key));
    });
  }

  async function preview(draft: GrantDraft): Promise<GrantPreview> {
    const role = roleRefs.find((r) => r.id === draft.roleId);
    const people = await expand(draft.principals);
    return {
      gaining: people.map((principal) => ({ principal, permissions: role?.permissions ?? [] })),
      already: [],
      approximate:
        "Lists what the role carries, not what is new to each person, and does not yet say who already has it: the backend has no grant preview.",
    };
  }

  async function onSubmit(draft: GrantDraft): Promise<GrantOutcome[]> {
    if (!draft.roleId || !draft.scope) return [];
    const people = await expand(draft.principals);
    const outcomes: GrantOutcome[] = [];
    for (const principal of people) {
      if (principal.kind !== "user") {
        outcomes.push({
          principal,
          ok: false,
          error: `A ${principal.kind} cannot hold a role yet: the backend grants to users only.`,
        });
        continue;
      }
      try {
        const { data } = await grantRole({
          variables: {
            input: {
              userId: principal.id,
              roleId: draft.roleId,
              scopeKind: draft.scope.kind,
              scopeGuid: draft.scope.id,
            },
          },
        });
        const r = data?.grantRole;
        outcomes.push(
          r?.ok
            ? { principal, ok: true }
            : { principal, ok: false, error: r?.errors?.[0]?.message ?? "The grant failed" }
        );
      } catch (err) {
        outcomes.push({
          principal,
          ok: false,
          error: err instanceof Error ? err.message : "The grant failed",
        });
      }
    }
    if (outcomes.some((o) => o.ok)) {
      await client.refetchQueries({
        include: [LIST_ROLE_BINDINGS, LIST_ROLE_BINDINGS_PAGE, LIST_MEMBERS],
      });
    }
    return outcomes;
  }

  const searchError = users.error ?? teams.error;
  return {
    search: {
      query,
      setQuery,
      results,
      loading: Boolean(term) && (users.loading || teams.loading) && results.length === 0,
      error: searchError ? { message: searchError.message } : null,
    },
    roles: roleRefs,
    rolesLoading: roles.loading && !roles.data,
    rolesError: roles.error ? { message: roles.error.message } : null,
    scopeTree,
    preview,
    onSubmit,
    expirySupported: false,
  };
}
