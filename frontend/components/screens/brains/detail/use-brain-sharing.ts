"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import type { Principal } from "@/components/access/access-model";
import { useCursorFeed } from "@/components/feed/use-cursor-feed";
import { selectRows } from "@/components/list/select-rows";
import { useListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import type {
  ActionSafetyQuery,
  ListAuditEventsPageQuery,
  ListAuditEventsPageQueryVariables,
  ListTeamsPageQuery,
  ListTeamsPageQueryVariables,
  ShareBrainMutation,
  ShareBrainMutationVariables,
  UnshareBrainMutation,
  UnshareBrainMutationVariables,
} from "@/graphql/__generated__/operations";
import { LIST_TEAMS_PAGE } from "@/graphql/identity/identity.queries";
import { LIST_AUDIT_EVENTS_PAGE } from "@/graphql/operations/operations.queries";
import { ACTION_SAFETY } from "@/graphql/permissions/astrolift.queries";
import { SHARE_BRAIN, UNSHARE_BRAIN } from "@/graphql/registry/brains.mutations";
import { useDebounce } from "@/hooks/use-debounce";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { gatedWrite } from "../gated-write";
import { BRAIN_SHARES_LIST, BRAIN_SHARES_SELECT, type ShareRow } from "./brain-detail-lists";
import { gateAsk, historyEntry, shareHistoryFilter, type ShareLevel } from "./brain-detail-model";
import { activeBrainSection, brainSections } from "./brain-tabs-model";
import type { BrainSharingTabProps } from "./BrainSharingTab";
import type { FramedBrainValue } from "./use-brain-frame";

type AuditEvent = ListAuditEventsPageQuery["astroliftAuditEventsPage"]["items"][number];

const TEAM_RESULTS = 20;
const HISTORY_PAGE = 25;

/**
 * The data half of the Sharing tab (#2427): the layer's shares (they come
 * whole with the layer, so the list's search and pages are made here), a
 * team search for Share, shareBrain and unshareBrain (a share asks for a
 * fresh sign-in; the app-wide prompt opens and the write is sent once
 * more), what brain.share asks here, and the access history from the audit
 * trail. Each section reads only what it shows.
 */
export function useBrainSharing(framed: FramedBrainValue): BrainSharingTabProps {
  const t = useTranslations("brains.sharing");
  const { brain, can } = framed;
  const params = useSearchParams();
  const section = activeBrainSection("sharing", Object.fromEntries(params?.entries() ?? [])) as
    | "teams"
    | "history";
  const onTeams = section === "teams";

  const list = useListState(useLocalizedList(BRAIN_SHARES_LIST));
  const { state } = list;
  const page = React.useMemo(
    () =>
      selectRows(
        brain.sharedWith,
        {
          filters: list.filters,
          q: state.q,
          sort: state.sort,
          page: state.page,
          pageSize: state.pageSize,
        },
        BRAIN_SHARES_SELECT
      ),
    [brain.sharedWith, list.filters, state.q, state.sort, state.page, state.pageSize]
  );

  const [query, setQuery] = React.useState("");
  const search = useDebounce(query.trim(), 250);
  const teams = useQuery<ListTeamsPageQuery, ListTeamsPageQueryVariables>(LIST_TEAMS_PAGE, {
    variables: { search: search || null, limit: TEAM_RESULTS },
    skip: !onTeams || !can.share,
    fetchPolicy: "cache-first",
  });
  const sharedLevel = new Map(brain.sharedWith.map((s) => [s.team, s.access]));
  const results: Principal[] = (teams.data?.astroliftTeamsPage.items ?? [])
    .filter((team) => team.slug !== brain.team)
    .map((team) => ({
      kind: "team",
      id: team.slug,
      name: team.name || team.slug,
      detail: sharedLevel.has(team.slug)
        ? t("alreadyShared", { level: sharedLevel.get(team.slug)! })
        : undefined,
    }));
  const [team, setTeam] = React.useState<Principal | null>(null);
  const [level, setLevel] = React.useState<ShareLevel>("viewer");

  const perms = useMyPermissions();
  const safety = useQuery<ActionSafetyQuery>(ACTION_SAFETY, {
    variables: { appSlug: brain.slug },
    skip: !onTeams || !can.share || perms.loading || !perms.can("org.read"),
    fetchPolicy: "cache-first",
  });
  const ask = gateAsk(safety.data?.astroliftActionSafety, "brain.share");

  const refetch = { refetchQueries: ["GetBrain"], awaitRefetchQueries: true };
  const [shareBrain, shareState] = useMutation<ShareBrainMutation, ShareBrainMutationVariables>(
    SHARE_BRAIN,
    refetch
  );
  const [unshareBrain, unshareState] = useMutation<
    UnshareBrainMutation,
    UnshareBrainMutationVariables
  >(UNSHARE_BRAIN, refetch);
  const [refusal, setRefusal] = React.useState<{ message: string; approval: boolean } | null>(null);

  async function share(teamSlug: string, to: ShareLevel): Promise<boolean> {
    const outcome = await gatedWrite(
      async () =>
        (
          await shareBrain({
            variables: {
              input: {
                slug: brain.slug,
                teamSlug,
                agentSlug: null,
                access: to === "deployer" ? "DEPLOYER" : "VIEWER",
              },
            },
          })
        ).data?.shareBrain,
      t("shareFailed")
    );
    if (!outcome.ok) {
      setRefusal({ message: outcome.message, approval: outcome.code === "APPROVAL_REQUIRED" });
      return false;
    }
    setRefusal(null);
    toast.success(t("shared", { team: teamSlug, level: t(`levels.${to}`) }));
    return true;
  }

  const feed = useCursorFeed<
    ListAuditEventsPageQuery,
    AuditEvent,
    ListAuditEventsPageQueryVariables
  >(LIST_AUDIT_EVENTS_PAGE, {
    variables: { filter: shareHistoryFilter(brain.slug) },
    select: (d) => d?.astroliftAuditEventsPage,
    keyOf: (e) => e.id,
    pageSize: HISTORY_PAGE,
    skip: onTeams || !can.readAudit,
    live: false,
  });
  const history = feed.feed;

  return {
    layerName: brain.name || brain.slug,
    homeTeam: brain.team,
    teamsCardHref: `/apps/${encodeURIComponent(brain.slug)}/settings`,
    section,
    sections: brainSections(brain.slug, "sharing", (key) => t(`sectionLabels.${key}`)),
    list,
    rows: page.rows,
    totalCount: page.totalCount,
    canShare: can.share,
    canShareAsEditor: can.shareAsEditor,
    ask,
    share: {
      team,
      onTeam: setTeam,
      search: {
        query,
        setQuery,
        results,
        loading: teams.loading && !teams.data,
        error: teams.error && !teams.data ? { message: teams.error.message } : null,
      },
      level,
      onLevel: setLevel,
      submitting: shareState.loading || unshareState.loading,
      onShare: async () => {
        if (!team) return false;
        const applied = await share(team.id, level);
        if (applied) setTeam(null);
        return applied;
      },
    },
    onChangeLevel: (row: ShareRow, to: ShareLevel) => share(row.team, to),
    onUnshare: async (row: ShareRow) => {
      const outcome = await gatedWrite(
        async () =>
          (
            await unshareBrain({
              variables: { input: { slug: brain.slug, teamSlug: row.team, agentSlug: null } },
            })
          ).data?.unshareBrain,
        t("unshareFailed")
      );
      if (!outcome.ok) {
        setRefusal({ message: outcome.message, approval: false });
        return false;
      }
      setRefusal(null);
      toast.success(t("unshared", { team: row.team }));
      return true;
    },
    refusal,
    history: {
      allowed: can.readAudit,
      items: history.items.map(historyEntry),
      loading: history.loading,
      error: history.error,
      onRetry: history.onRetry,
      hasMore: history.hasMore,
      loadingMore: history.loadingMore,
      onLoadMore: history.onLoadMore,
    },
  };
}
