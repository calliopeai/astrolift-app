"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
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

/** The data half of AlertsScreen: both table walks, the stat counts, and every mutation. */
export function useAlerts() {
  const t = useTranslations("lists.alerts");

  // Both page fields take `search`; neither takes a sort argument, so no
  // column declares a `sortKey`. The comparators this file used to run
  // (name / severity / created) only ever reordered the rows already in
  // hand, which is the wrong order at every page boundary.
  const rulesTable = useCursorTable<AlertRule>({
    query: LIST_ALERT_RULES_PAGE,
    variables: { activeOnly: false },
    extract: (d) => (d as RulesPageResp | undefined)?.astroliftAlertRulesPage,
    searchVariable: "search",
    urlKey: "rule",
  });

  const eventsTable = useCursorTable<AlertEvent>({
    query: LIST_ALERT_EVENTS_PAGE,
    variables: { unresolvedOnly: false },
    extract: (d) => (d as EventsPageResp | undefined)?.astroliftAlertEventsPage,
    searchVariable: "search",
    urlKey: "event",
    pollInterval: 30000,
  });

  // Stat-card counts. `totalCount` is computed over the whole filtered
  // set, so `limit: 1` buys the number without the rows — the cards used
  // to count a capped array in the browser, which stopped being true at
  // the 201st rule and the 101st event.
  const activeRules = useQuery<RulesPageResp>(LIST_ALERT_RULES_PAGE, {
    variables: { activeOnly: true, limit: 1 },
    fetchPolicy: "cache-and-network",
  });
  const unresolved = useQuery<EventsPageResp>(LIST_ALERT_EVENTS_PAGE, {
    variables: { unresolvedOnly: true, limit: 1 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });

  const ruleCount = rulesTable.totalCount ?? 0;
  const activeRuleCount = activeRules.data?.astroliftAlertRulesPage.totalCount ?? 0;
  const unresolvedCount = unresolved.data?.astroliftAlertEventsPage.totalCount ?? 0;
  const eventCount = eventsTable.totalCount ?? 0;

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
  const [ackEvent, ackState] = useMutation<{
    acknowledgeAlertEvent: MutationResult<AlertEvent>;
  }>(ACKNOWLEDGE_ALERT_EVENT, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
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
    createState.loading ||
    deleteState.loading ||
    ackState.loading ||
    muteState.loading ||
    unmuteState.loading;

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

  async function acknowledge(e: AlertEvent) {
    const { data } = await ackEvent({ variables: { input: { id: e.id } } });
    if (!data?.acknowledgeAlertEvent.ok) {
      toast.error(data?.acknowledgeAlertEvent.errors?.[0]?.message ?? "Ack failed");
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

  return {
    rulesTable,
    eventsTable,
    ruleCount,
    activeRuleCount,
    unresolvedCount,
    eventCount,
    busy,
    createRule,
    deleteRule,
    acknowledge,
    mutePreset,
    muteCustom,
    unmute,
  };
}
