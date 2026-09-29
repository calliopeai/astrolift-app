"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useCursorFeed } from "@/components/feed/use-cursor-feed";
import { useListState } from "@/components/list/use-list-state";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ACKNOWLEDGE_ALERT_EVENT,
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_EVENTS_PAGE,
  LIST_ALERT_RULES_PAGE,
  MUTE_ALERT_RULE,
  UNMUTE_ALERT_RULE,
} from "@/graphql/operations/alerts.queries";

import { formatDurationSeconds } from "./alert-format";
import {
  ALERT_RULES_LIST,
  type AlertEventsView,
  narrowRules,
  narrows,
  rulesVariables,
} from "./alerts-list";

export interface AlertMute {
  id: string;
  ttlUntil: string;
  reason: string;
  createdBy: string;
}

export interface AlertRule {
  id: string;
  name: string;
  target: string;
  targetId: string;
  severity: string;
  predicate: Record<string, unknown>;
  notifyChannels: Record<string, unknown>;
  isActive: boolean;
  organizationSlug: string;
  createdAt: string;
  updatedAt: string;
  activeMute: AlertMute | null;
}

export interface AlertEvent {
  id: string;
  ruleId: string;
  severity: string;
  firedAt: string;
  resolvedAt?: string | null;
  acknowledgedAt?: string | null;
  summary: string;
  detail: Record<string, unknown>;
}

export interface CreateAlertRuleInput {
  name: string;
  target: string;
  targetId: string | null;
  severity: string;
  predicate: Record<string, unknown> | null;
  notifyChannels: Record<string, unknown> | null;
}

interface RulesPageResp {
  astroliftAlertRulesPage: CursorPage<AlertRule>;
}
interface EventsPageResp {
  astroliftAlertEventsPage: CursorPage<AlertEvent>;
}

/**
 * The data half of AlertsScreen: URL list state and one cursor page of
 * rules (severity and Muted narrow a wider page, see alerts-list.ts), the
 * two stat counts, and the rule mutations. Events are their own route
 * (useAlertEvents), so this page never fetches them as a list.
 */
export function useAlerts() {
  const t = useTranslations("lists.alerts");

  const list = useListState(ALERT_RULES_LIST);
  const rules = useQuery<RulesPageResp>(LIST_ALERT_RULES_PAGE, {
    variables: rulesVariables(list.filters, list.state),
    fetchPolicy: "cache-and-network",
  });
  const rulesData = rules.data ?? rules.previousData;
  const rulesPage = rulesData?.astroliftAlertRulesPage;

  // Stat counts. `totalCount` is computed over the whole filtered set, so
  // `limit: 1` buys the number without the rows.
  const activeRules = useQuery<RulesPageResp>(LIST_ALERT_RULES_PAGE, {
    variables: { activeOnly: true, limit: 1 },
    fetchPolicy: "cache-and-network",
  });
  const unresolved = useQuery<EventsPageResp>(LIST_ALERT_EVENTS_PAGE, {
    variables: { unresolvedOnly: true, limit: 1 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });

  const activeRuleCount = activeRules.data?.astroliftAlertRulesPage.totalCount ?? 0;
  const unresolvedCount = unresolved.data?.astroliftAlertEventsPage.totalCount ?? 0;

  // Refetch by operation name: every mutation below moves rows in both
  // walks *and* in the two count queries, which are the same documents at
  // different variables. A `{ query, variables }` entry would refresh one
  // variable set and leave the others stale.
  const refetch = ["ListAlertRulesPage", "ListAlertEventsPage"];

  const [createRuleMutation, createState] = useMutation<{
    createAlertRule: MutationResult<AlertRule>;
  }>(CREATE_ALERT_RULE, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [deleteRuleMutation, deleteState] = useMutation<{
    deleteAlertRule: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_ALERT_RULE, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [muteRule, muteState] = useMutation<{
    muteAlertRule: MutationResult<AlertRule>;
  }>(MUTE_ALERT_RULE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [unmuteRule, unmuteState] = useMutation<{
    unmuteAlertRule: MutationResult<AlertRule>;
  }>(UNMUTE_ALERT_RULE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const busy =
    createState.loading || deleteState.loading || muteState.loading || unmuteState.loading;

  /** True when the rule was created, so the sheet can close. */
  async function createRule(input: CreateAlertRuleInput): Promise<boolean> {
    const { data } = await createRuleMutation({ variables: { input } });
    if (data?.createAlertRule.ok) {
      toast.success(`Created ${input.name}`);
      return true;
    }
    toast.error(data?.createAlertRule.errors?.[0]?.message ?? "Create failed");
    return false;
  }

  /** Throws on failure so the confirm dialog shows the error inline. */
  async function deleteRule(r: AlertRule) {
    const { data } = await deleteRuleMutation({ variables: { input: { id: r.id } } });
    if (data?.deleteAlertRule.ok) {
      toast.success(`Deleted ${r.name}`);
    } else {
      throw new Error(data?.deleteAlertRule.errors?.[0]?.message ?? "Delete failed");
    }
  }

  async function mutePreset(r: AlertRule, durationHours: number, durationLabel: string) {
    const reason = `Quick mute (${durationLabel})`;
    const { data } = await muteRule({
      variables: {
        input: {
          ruleId: r.id,
          durationSeconds: Math.round(durationHours * 3600),
          reason,
        },
      },
    });
    if (data?.muteAlertRule.ok) {
      toast.success(t("mute.toastMuted", { name: r.name, duration: durationLabel }));
    } else {
      toast.error(data?.muteAlertRule.errors?.[0]?.message ?? t("mute.toastMuteFailed"));
    }
  }

  /** True when the rule was muted, so the sheet can close. */
  async function muteCustom(
    r: AlertRule,
    durationSeconds: number,
    reason: string
  ): Promise<boolean> {
    const { data } = await muteRule({
      variables: {
        input: { ruleId: r.id, durationSeconds, reason },
      },
    });
    if (data?.muteAlertRule.ok) {
      toast.success(
        t("mute.toastMuted", {
          name: r.name,
          duration: formatDurationSeconds(durationSeconds),
        })
      );
      return true;
    }
    toast.error(data?.muteAlertRule.errors?.[0]?.message ?? t("mute.toastMuteFailed"));
    return false;
  }

  async function unmute(r: AlertRule) {
    const { data } = await unmuteRule({
      variables: { input: { ruleId: r.id } },
    });
    if (data?.unmuteAlertRule.ok) {
      toast.success(t("mute.toastUnmuted", { name: r.name }));
    } else {
      toast.error(data?.unmuteAlertRule.errors?.[0]?.message ?? t("mute.toastUnmuteFailed"));
    }
  }

  const narrowed = narrows(list.filters);
  return {
    list,
    rows: narrowRules(rulesPage?.items ?? [], list.filters),
    totalCount: narrowed ? null : (rulesPage?.totalCount ?? null),
    nextCursor: rulesPage?.nextCursor ?? null,
    loading: rules.loading && !rulesData,
    error: rules.error && !rulesData ? { message: rules.error.message } : null,
    onRetry: () => {
      void rules.refetch();
    },
    activeRuleCount,
    unresolvedCount,
    busy,
    createRule,
    deleteRule,
    mutePreset,
    muteCustom,
    unmute,
  };
}

/**
 * Admin › Alerts › Events: the firing instances as a Feed on
 * `astroliftAlertEventsPage`, All or Firing, polled, new ones held behind
 * the pill, and Ack. The data half of AlertEventsScreen.
 */
export function useAlertEvents(view: AlertEventsView) {
  const { feed } = useCursorFeed<EventsPageResp, AlertEvent>(LIST_ALERT_EVENTS_PAGE, {
    variables: { unresolvedOnly: view === "firing", search: null },
    select: (d) => d?.astroliftAlertEventsPage,
    keyOf: (e) => e.id,
    pollInterval: 30000,
  });

  const [ackEvent, ackState] = useMutation<{
    acknowledgeAlertEvent: MutationResult<AlertEvent>;
  }>(ACKNOWLEDGE_ALERT_EVENT, {
    refetchQueries: ["ListAlertEventsPage"],
    awaitRefetchQueries: true,
  });

  async function acknowledge(e: AlertEvent) {
    const { data } = await ackEvent({ variables: { input: { id: e.id } } });
    if (!data?.acknowledgeAlertEvent.ok) {
      toast.error(data?.acknowledgeAlertEvent.errors?.[0]?.message ?? "Ack failed");
    }
  }

  return { view, events: feed, busy: ackState.loading, acknowledge };
}
