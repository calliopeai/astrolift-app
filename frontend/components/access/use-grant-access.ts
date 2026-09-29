"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";

import type {
  ListRolesICanGrantQuery,
  ListTeamMembersQuery,
} from "@/graphql/__generated__/operations";
import { GRANT_PREVIEW, PRINCIPAL_SEARCH } from "@/graphql/access/access.queries";
import { GRANT_ROLE } from "@/graphql/identity/identity.mutations";
import {
  LIST_MEMBERS,
  LIST_ROLE_BINDINGS,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_ROLES_I_CAN_GRANT,
  LIST_TEAM_MEMBERS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftRoleBinding,
  MutationResult,
  ScopeKind,
} from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";

import type { GrantSourceInfo, Principal, RoleRef } from "./access-model";
import {
  expiryToIso,
  type GrantDraft,
  type GrantOutcome,
  type GrantPreview,
} from "./GrantAccessFlow";
import { useScopeTree } from "./use-scope-tree";

const SEARCH_LIMIT = 10;
/** How many people the preview lists on each side; the counts are exact beyond it. */
const PREVIEW_LIMIT = 50;

/** An `astroliftPrincipalSearch` row, as far as the Who step reads it. */
export interface SearchedPrincipal {
  kind: string;
  name: string;
  secondary: string;
  userId?: string | null;
  groupExternalId?: string | null;
  memberCount?: number | null;
  teamId?: string | null;
  teamSlug?: string | null;
}

/** A searched user, group or team as a pick; anything else (an invitation) is not one. */
export function principalOfSearch(p: SearchedPrincipal): Principal | null {
  if (p.kind === "USER" && p.userId) {
    return { kind: "user", id: p.userId, name: p.name, detail: p.secondary || undefined };
  }
  if (p.kind === "GROUP" && p.groupExternalId) {
    const n = p.memberCount ?? 0;
    return {
      kind: "group",
      id: p.groupExternalId,
      name: p.name,
      detail: `IdP group · ${n} ${n === 1 ? "member" : "members"}`,
    };
  }
  if (p.kind === "TEAM" && p.teamId) {
    return { kind: "team", id: p.teamId, name: p.name, detail: p.teamSlug ?? undefined };
  }
  return null;
}

/** A pick as `AstroliftPrincipalRef`: the user id, the group's external id, the team id. */
export function principalRef(p: Principal): { kind: string; id: string } {
  return { kind: p.kind.toUpperCase(), id: p.id };
}

interface PreviewSource {
  source: string;
  scopeKind?: string | null;
  scopeGuid?: string | null;
  sourceScopeLabel: string;
  groupExternalId?: string | null;
  teamSlug?: string | null;
  inherited: boolean;
}

interface PreviewPerson {
  user: { id: string; username: string; email: string };
  gained: string[];
  via: PreviewSource[];
}

interface PreviewResp {
  astroliftGrantPreview: {
    ok: boolean;
    errors: string[];
    gainingCount: number;
    unchangedCount: number;
    gaining: PreviewPerson[];
    unchanged: PreviewPerson[];
    groups: { groupExternalId: string; memberCount: number }[];
    allowed: boolean;
    refusal?: string | null;
    notes: string[];
  };
}

/** Where someone already has it from: the first of their other grants that carries it. */
export function sourceOfPreview(via: PreviewSource | undefined): GrantSourceInfo {
  if (!via) return {};
  const source: GrantSourceInfo = {};
  if ((via.source === "GROUP_BINDING" || via.source === "GROUP_MAPPING") && via.groupExternalId) {
    source.via = { kind: "group", group: via.groupExternalId };
  } else if (via.source === "TEAM_SHARE" && via.teamSlug) {
    source.via = { kind: "team", team: via.teamSlug };
  }
  if (via.inherited && via.scopeKind) {
    source.inheritedFrom = {
      kind: via.scopeKind as ScopeKind,
      id: via.scopeGuid ?? via.sourceScopeLabel,
      name: via.sourceScopeLabel,
    };
  }
  return source;
}

/** The server's preview as the flow's review. */
export function previewOf(p: PreviewResp["astroliftGrantPreview"]): GrantPreview {
  const person = (u: PreviewPerson["user"]): Principal => ({
    kind: "user",
    id: u.id,
    name: u.username,
    detail: u.email || undefined,
  });
  return {
    gaining: p.gaining.map((g) => ({ principal: person(g.user), permissions: g.gained })),
    already: p.unchanged.map((u) => ({
      principal: person(u.user),
      source: sourceOfPreview(u.via[0]),
    })),
    gainingCount: p.gainingCount,
    alreadyCount: p.unchangedCount,
    groups: p.groups,
    refusal: p.allowed ? null : (p.refusal ?? "You cannot grant this role here."),
    notes: p.notes,
  };
}

/**
 * The data half of `GrantAccessFlow`:
 *
 * - who: `astroliftPrincipalSearch` for users, IdP groups and teams, on the
 *   debounced query;
 * - roles: `astroliftRolesICanGrant`, so every role offered can be granted;
 * - scope: the nav tree, cache-first (`useScopeTree`);
 * - preview: `astroliftGrantPreview`, the server's answer to who gains what,
 *   who already had it and through what, and whether the caller may grant it;
 * - submit: one `grantRole` per user or group (a group grant reaches everyone
 *   the identity provider puts in it), a team expanded to its members at the
 *   time of the grant, each with the picked expiry.
 */
export function useGrantAccess() {
  const client = useApolloClient();
  const [query, setQuery] = React.useState("");
  const term = useDebounce(query.trim(), 250);

  const search = useQuery<{ astroliftPrincipalSearch: { items: SearchedPrincipal[] } }>(
    PRINCIPAL_SEARCH,
    {
      variables: {
        search: term,
        filter: { kind: ["USER", "GROUP", "TEAM"] },
        page: 1,
        pageSize: SEARCH_LIMIT,
      },
      skip: !term,
      fetchPolicy: "cache-first",
    }
  );
  const roles = useQuery<ListRolesICanGrantQuery>(LIST_ROLES_I_CAN_GRANT, {
    fetchPolicy: "cache-and-network",
  });
  const scopeTree = useScopeTree();
  const [grantRole] = useMutation<{
    grantRole: MutationResult<AstroliftRoleBinding>;
  }>(GRANT_ROLE);

  const results: Principal[] = term
    ? (search.data?.astroliftPrincipalSearch.items ?? [])
        .map(principalOfSearch)
        .filter((p): p is Principal => p !== null)
    : [];

  const roleRefs: RoleRef[] = (roles.data?.astroliftRolesICanGrant ?? []).map((r) => ({
    ...r,
    scopeLevel: r.scopeLevel as ScopeKind,
  }));

  /** Teams become their members, now; users and groups stay as they are. */
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
    const { data } = await client.query<PreviewResp>({
      query: GRANT_PREVIEW,
      variables: {
        input: {
          action: "GRANT",
          principals: draft.principals.map(principalRef),
          roleId: draft.roleId,
          scopeKind: draft.scope?.kind ?? null,
          scopeId: draft.scope?.id ?? null,
          expiresAt: expiryToIso(draft.expiry, Date.now()),
        },
        limit: PREVIEW_LIMIT,
      },
      fetchPolicy: "network-only",
    });
    const p = data?.astroliftGrantPreview;
    if (!p) throw new Error("Could not preview the grant");
    if (!p.ok) throw new Error(p.errors.join(" ") || "Could not preview the grant");
    return previewOf(p);
  }

  async function onSubmit(draft: GrantDraft): Promise<GrantOutcome[]> {
    if (!draft.roleId || !draft.scope) return [];
    const expiresAt = expiryToIso(draft.expiry, Date.now());
    const holders = await expand(draft.principals);
    const outcomes: GrantOutcome[] = [];
    for (const principal of holders) {
      if (principal.kind !== "user" && principal.kind !== "group") {
        outcomes.push({ principal, ok: false, error: `A ${principal.kind} cannot hold a role.` });
        continue;
      }
      try {
        const { data } = await grantRole({
          variables: {
            input: {
              ...(principal.kind === "user"
                ? { userId: principal.id }
                : { groupExternalId: principal.id }),
              roleId: draft.roleId,
              scopeKind: draft.scope.kind,
              scopeGuid: draft.scope.id,
              expiresAt,
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
        include: [LIST_ROLE_BINDINGS, LIST_ROLE_BINDINGS_PAGE, LIST_MEMBERS, "AccessOn"],
      });
    }
    return outcomes;
  }

  return {
    search: {
      query,
      setQuery,
      results,
      loading: Boolean(term) && search.loading && results.length === 0,
      error: search.error ? { message: search.error.message } : null,
    },
    roles: roleRefs,
    rolesLoading: roles.loading && !roles.data,
    rolesError: roles.error ? { message: roles.error.message } : null,
    scopeTree,
    preview,
    onSubmit,
    expirySupported: true,
  };
}
