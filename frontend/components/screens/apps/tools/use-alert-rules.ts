"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ACKNOWLEDGE_ALERT_EVENT,
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_EVENTS,
  LIST_ALERT_RULES,
  MUTE_ALERT_RULE,
  UNMUTE_ALERT_RULE,
} from "@/graphql/operations/alerts.queries";

// #648 — Alert-rules panel typings. Mirrors the AlertsClient at
// app/(app)/alerts/alerts-client.tsx; declared locally here because the
// repo intentionally types each consumer rather than re-exporting from
// the operations module (predicate/notifyChannels are scalar JSON on
// the wire so each surface narrows them to its own shape).
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
  notifyChannels: Array<Record<string, unknown>> | Record<string, unknown>;
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
  targetId: string;
  severity: string;
  predicate: Record<string, unknown>;
  notifyChannels: Array<Record<string, unknown>>;
  isActive: boolean;
}

interface AlertRulesResp {
  astroliftAlertRules: AlertRule[];
}

interface AlertEventsResp {
  astroliftAlertEvents: AlertEvent[];
}

/** #648 — an app's alert rules and the create / mute / unmute / delete actions. */
export function useAlertRules(appId: string) {
  const refetchVars = React.useMemo(
    () => ({ target: "app", targetId: appId, activeOnly: false }),
    [appId]
  );

  const rules = useQuery<AlertRulesResp>(LIST_ALERT_RULES, {
    variables: refetchVars,
    fetchPolicy: "cache-and-network",
  });

  const refetchQueries = React.useMemo(
    () => [{ query: LIST_ALERT_RULES, variables: refetchVars }],
    [refetchVars]
  );

  const [createRule, createState] = useMutation<{
    createAlertRule: MutationResult<AlertRule>;
  }>(CREATE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });
  const [deleteRule, deleteState] = useMutation<{
    deleteAlertRule: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });
  const [muteRule, muteState] = useMutation<{
    muteAlertRule: MutationResult<AlertRule>;
  }>(MUTE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });
  const [unmuteRule, unmuteState] = useMutation<{
    unmuteAlertRule: MutationResult<AlertRule>;
  }>(UNMUTE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });

  const busy =
    createState.loading || deleteState.loading || muteState.loading || unmuteState.loading;

  const ruleList = rules.data?.astroliftAlertRules ?? [];

  async function onUnmute(r: AlertRule) {
    const { data } = await unmuteRule({ variables: { input: { ruleId: r.id } } });
    if (data?.unmuteAlertRule.ok) {
      toast.success(`Unmuted ${r.name}`);
    } else {
      toast.error(data?.unmuteAlertRule.errors?.[0]?.message ?? "Unmute failed");
    }
  }

  /** Resolves true when the rule was created (the view closes its sheet). */
  async function onCreate(input: CreateAlertRuleInput): Promise<boolean> {
    const { data } = await createRule({ variables: { input } });
    if (data?.createAlertRule.ok) {
      toast.success(`Created ${input.name}`);
      return true;
    }
    toast.error(data?.createAlertRule.errors?.[0]?.message ?? "Create failed");
    return false;
  }

  /** Resolves true when the rule was muted (the view closes its sheet). */
  async function onMute(r: AlertRule, durationSeconds: number, reason: string): Promise<boolean> {
    const { data } = await muteRule({
      variables: { input: { ruleId: r.id, durationSeconds, reason } },
    });
    if (data?.muteAlertRule.ok) {
      toast.success(`Muted ${r.name}`);
      return true;
    }
    toast.error(data?.muteAlertRule.errors?.[0]?.message ?? "Mute failed");
    return false;
  }

  /** Throws on failure so the confirm dialog stays open with the error. */
  async function onDelete(r: AlertRule): Promise<void> {
    const { data } = await deleteRule({
      variables: { input: { id: r.id } },
    });
    if (!data?.deleteAlertRule.ok) {
      throw new Error(data?.deleteAlertRule.errors?.[0]?.message ?? "Delete failed");
    }
    toast.success(`Deleted ${r.name}`);
  }

  return {
    appId,
    rules: ruleList,
    loading: rules.loading && ruleList.length === 0,
    busy,
    creating: createState.loading,
    muting: muteState.loading,
    onCreate,
    onMute,
    onUnmute,
    onDelete,
  };
}

/** The last five events for one alert rule, plus acknowledge. */
export function useAlertEvents(ruleId: string) {
  const events = useQuery<AlertEventsResp>(LIST_ALERT_EVENTS, {
    variables: { ruleId, unresolvedOnly: false, limit: 5 },
    fetchPolicy: "cache-and-network",
  });

  const [ackEvent, ackState] = useMutation<{
    acknowledgeAlertEvent: MutationResult<AlertEvent>;
  }>(ACKNOWLEDGE_ALERT_EVENT, {
    refetchQueries: [
      { query: LIST_ALERT_EVENTS, variables: { ruleId, unresolvedOnly: false, limit: 5 } },
    ],
    awaitRefetchQueries: true,
  });

  async function onAck(e: AlertEvent) {
    const { data } = await ackEvent({ variables: { input: { id: e.id } } });
    if (!data?.acknowledgeAlertEvent.ok) {
      toast.error(data?.acknowledgeAlertEvent.errors?.[0]?.message ?? "Ack failed");
    }
  }

  const eventList = events.data?.astroliftAlertEvents ?? [];

  return {
    events: eventList,
    loading: events.loading && eventList.length === 0,
    acking: ackState.loading,
    onAck,
  };
}
