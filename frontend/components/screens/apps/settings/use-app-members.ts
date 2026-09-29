"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { usePathname } from "next/navigation";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { grantHref } from "@/components/screens/administration/access/access-nav";
import { checkAccessHref } from "@/components/screens/administration/permissions/check-access-query";
import { REVOKE_ROLE_BINDING } from "@/graphql/identity/identity.mutations";
import { LIST_ROLE_BINDINGS, LIST_ROLE_BINDINGS_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftRoleBinding, MutationResult } from "@/graphql/identity/identity.types";
import { REVOKE_TEAM_ACCESS_FROM_APP } from "@/graphql/registry/registry.mutations";
import {
  GET_APP,
  LIST_APP_TEAM_ACCESSES,
  LIST_APP_TEAM_ACCESSES_PAGE,
} from "@/graphql/registry/registry.queries";
import type {
  AstroliftAppTeamAccess,
  AstroliftRegisteredApp,
} from "@/graphql/registry/registry.types";
import { GET_ME } from "@/graphql/user/user.queries";
import type { CurrentUser } from "@/graphql/user/user.types";

import { type AccessRow, accessRows, APP_ACCESS_LIST, viewSources } from "./app-access-rows";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface BindingsPageResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}
interface SharesPageResp {
  astroliftAppTeamAccessesPage: CursorPage<AstroliftAppTeamAccess>;
}
interface MeResp {
  me: CurrentUser | null;
}

/** All's first page carries every team share above the bindings; an app has a handful. */
const SHARES_ON_ALL = 100;

/**
 * The data half of the app's (and agent's) People with access (design
 * 3.3): list state in the URL, and only the sources the active view shows.
 * Role bindings come from `astroliftRoleBindingsPage` narrowed by `appSlug`
 * (#1241), team shares from `astroliftAppTeamAccessesPage`; each is skipped
 * in a view that does not show it, and both page on the server. Remove
 * acts at the grant's source: revoke the binding, or end the team share.
 */
export function useAppMembers(slug: string) {
  const pathname = usePathname() ?? `/apps/${slug}/access`;
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const me = useQuery<MeResp>(GET_ME);
  const list = useListState(APP_ACCESS_LIST);
  const { state } = list;
  const view = state.view;
  const sources = viewSources(view);
  const meUser = me.data?.me ?? null;
  const search = view === "mine" ? (meUser?.profile?.username ?? null) : state.q.trim() || null;
  const onShares = view === "teams";

  const bindings = useQuery<BindingsPageResp>(LIST_ROLE_BINDINGS_PAGE, {
    variables: { appSlug: slug, search, limit: state.pageSize, after: state.after },
    skip: !sources.bindings || (view === "mine" && !meUser),
    fetchPolicy: "cache-and-network",
  });
  const shares = useQuery<SharesPageResp>(LIST_APP_TEAM_ACCESSES_PAGE, {
    variables: {
      appSlug: slug,
      search: state.q.trim() || null,
      limit: onShares ? state.pageSize : SHARES_ON_ALL,
      after: onShares ? state.after : null,
    },
    skip: !sources.shares,
    fetchPolicy: "cache-and-network",
  });

  const [revokeBinding, { loading: revokingBinding }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    // The paginated document by operation name, so the revoke lands on the
    // cursor and search in effect, plus the deprecated flat list that
    // /members and the member detail page still read from the cache.
    refetchQueries: ["ListRoleBindingsPage", { query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });
  const [revokeShare, { loading: revokingShare }] = useMutation<{
    revokeTeamAccessFromApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_TEAM_ACCESS_FROM_APP, {
    // The overview's Teams card reads the flat list; refresh it with the page.
    refetchQueries: [
      "ListAppTeamAccessesPage",
      { query: LIST_APP_TEAM_ACCESSES, variables: { appSlug: slug } },
    ],
    awaitRefetchQueries: true,
  });

  const a = app.data?.astroliftApp ?? null;
  const bindingsPage = bindings.data?.astroliftRoleBindingsPage;
  const sharesPage = shares.data?.astroliftAppTeamAccessesPage;

  const rows = accessRows({
    view,
    firstPage: state.after === null,
    bindings: bindingsPage?.items ?? [],
    shares: sharesPage?.items ?? [],
    meId: meUser?.id ?? null,
  });

  const active = onShares ? shares : bindings;
  const error = (sources.bindings ? bindings.error : undefined) ?? shares.error;
  const counted = view === "mine" ? null : onShares ? sharesPage : bindingsPage;
  const totalCount =
    counted?.totalCount == null
      ? null
      : counted.totalCount + (view === "all" ? (sharesPage?.totalCount ?? 0) : 0);

  /** Throws on failure so the confirm dialog shows the error. */
  async function onRemove(row: AccessRow) {
    if (row.kind === "binding") {
      const { data } = await revokeBinding({ variables: { input: { id: row.binding.id } } });
      if (!data?.revokeRoleBinding.ok) {
        throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
      }
      toast.success("Role revoked");
      return;
    }
    const { data } = await revokeShare({
      variables: { input: { appId: row.share.appId, teamId: row.share.teamId } },
    });
    if (!data?.revokeTeamAccessFromApp.ok) {
      throw new Error(data?.revokeTeamAccessFromApp.errors?.[0]?.message ?? "Revoke failed");
    }
    toast.success(`Ended ${row.share.teamSlug}'s access`);
  }

  const scope = a ? { kind: "APP" as const, id: a.id, name: a.name || a.slug } : null;

  return {
    app: a,
    /** First load only. */
    loading: app.loading && !a,
    list,
    rows,
    rowsLoading: (active.loading || (view === "mine" && me.loading)) && rows.length === 0 && !error,
    stale: active.loading && rows.length > 0,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      if (sources.bindings) void bindings.refetch();
      if (sources.shares) void shares.refetch();
    },
    totalCount,
    nextCursor: (onShares ? sharesPage?.nextCursor : bindingsPage?.nextCursor) ?? null,
    removing: revokingBinding || revokingShare,
    onRemove,
    grantHref: grantHref({ scope: scope ?? undefined, returnTo: pathname }),
    checkHref: (row: AccessRow) =>
      checkAccessHref({ who: row.principal.kind === "user" ? row.principal.id : null, on: scope }),
  };
}
