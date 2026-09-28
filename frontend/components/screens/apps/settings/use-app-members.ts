"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { REVOKE_ROLE_BINDING } from "@/graphql/identity/identity.mutations";
import {
  LIST_ROLES,
  LIST_ROLE_BINDINGS,
  LIST_ROLE_BINDINGS_PAGE,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface BindingsPageResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

/**
 * The app members tab: the app, the APP-scope roles, the cursor table of
 * role bindings on this app, and revoke. The data half of AppMembersScreen.
 */
export function useAppMembers(slug: string) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const roles = useQuery<RolesResp>(LIST_ROLES);

  // `appSlug` narrows server-side to `scope_kind=APP, scope_id=<this app>`,
  // which is exactly what this page filtered for in the browser. The
  // argument was added to the field for this surface (#1241) and no
  // document had declared it, so the page went on fetching every binding
  // in the org to keep a handful. The field takes no sort argument, so no
  // column declares a `sortKey`.
  const table = useCursorTable<AstroliftRoleBinding>({
    query: LIST_ROLE_BINDINGS_PAGE,
    variables: { appSlug: slug },
    extract: (d) => (d as BindingsPageResp | undefined)?.astroliftRoleBindingsPage,
    searchVariable: "search",
    urlKey: "mem",
    fetchPolicy: "cache-and-network",
  });

  const [revoke, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    // The paginated document by operation name, so the revoke lands on the
    // cursor and search in effect, plus the deprecated flat list that
    // /members and the member detail page still read from the cache.
    refetchQueries: ["ListRoleBindingsPage", { query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });

  const a = app.data?.astroliftApp ?? null;

  // Roles relevant to APP scope.
  const appRoles = (roles.data?.astroliftRoles ?? []).filter((r) => r.scopeLevel === "APP");

  /** Throws on failure so the confirm dialog shows the error. */
  async function onRevoke(rb: AstroliftRoleBinding) {
    const { data } = await revoke({ variables: { input: { id: rb.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  return {
    app: a,
    /** First load only. */
    loading: app.loading && !a,
    appRoles,
    table,
    revoking,
    onRevoke,
  };
}
