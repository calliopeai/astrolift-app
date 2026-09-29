"use client";

/**
 * The data half of EmailDetailSheet: one hook per panel that talks to the
 * server. Each panel's container calls its hook only while the panel is
 * mounted, which is only once the detail query has answered.
 */

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { GET_COST_BY_BINDING } from "@/graphql/billing/billing.queries";
import type { AstroliftCostAttribution } from "@/graphql/billing/billing.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_RULES,
} from "@/graphql/operations/alerts.queries";
import {
  ADD_EMAIL_SUPPRESSION_ENTRY,
  CREATE_EMAIL_TEMPLATE,
  DELETE_EMAIL_TEMPLATE,
  REMOVE_EMAIL_SUPPRESSION_ENTRY,
  UPDATE_EMAIL_TEMPLATE,
  UPDATE_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import {
  GET_EMAIL_ENGAGEMENT_METRICS,
  GET_EMAIL_MESSAGES,
  GET_EMAIL_SERVICE_DETAIL,
  GET_EMAIL_TEMPLATE_STATS,
  GET_EMAIL_TEMPLATES,
} from "@/graphql/services/services.queries";
import type {
  AstroliftEmailEngagementMetrics,
  AstroliftEmailMessage,
  AstroliftEmailServiceDetail,
  AstroliftEmailSuppressionEntry,
  AstroliftEmailTemplate,
  AstroliftTemplateSendStatPoint,
} from "@/graphql/services/services.types";

// ── Detail (the sheet itself) ──────────────────────────────────────

interface DetailResp {
  astroliftEmailServiceDetail: AstroliftEmailServiceDetail | null;
}

/** The email-service detail payload behind the sheet. */
export function useEmailDetail(managedServiceId: string, open: boolean) {
  const { data, loading, refetch } = useQuery<DetailResp>(GET_EMAIL_SERVICE_DETAIL, {
    variables: { managedServiceId },
    skip: !open,
    fetchPolicy: "cache-and-network",
  });
  return {
    detail: data?.astroliftEmailServiceDetail ?? null,
    loading,
    onRefetch: () => {
      void refetch();
    },
  };
}

// ── Cost-per-period tile (#630) ────────────────────────────────────

interface CostByBindingResp {
  astroliftCostByBinding: AstroliftCostAttribution;
}

function sumForService(
  data: CostByBindingResp | undefined,
  managedServiceId: string
): number | null {
  const rows = data?.astroliftCostByBinding?.attributedRows;
  if (!rows) return null;
  const matches = rows.filter((r) => r.managedServiceId === managedServiceId);
  if (matches.length === 0) return 0;
  return matches.reduce((sum, r) => sum + r.amountCents, 0);
}

/** Month-to-date and trailing 30-day spend attributed to this service. */
export function useEmailCost(managedServiceId: string, appSlug: string, open: boolean) {
  const mtdQuery = useQuery<CostByBindingResp>(GET_COST_BY_BINDING, {
    variables: { window: "MTD", registeredAppSlug: appSlug },
    skip: !open || !appSlug,
    fetchPolicy: "cache-and-network",
  });
  const trailingQuery = useQuery<CostByBindingResp>(GET_COST_BY_BINDING, {
    variables: { days: 30, registeredAppSlug: appSlug },
    skip: !open || !appSlug,
    fetchPolicy: "cache-and-network",
  });

  const mtdSum = React.useMemo(
    () => sumForService(mtdQuery.data, managedServiceId),
    [mtdQuery.data, managedServiceId]
  );
  const trailingSum = React.useMemo(
    () => sumForService(trailingQuery.data, managedServiceId),
    [trailingQuery.data, managedServiceId]
  );

  const currency =
    mtdQuery.data?.astroliftCostByBinding?.currency ??
    trailingQuery.data?.astroliftCostByBinding?.currency ??
    "USD";

  return {
    mtdSum,
    trailingSum,
    currency,
    loading: mtdQuery.loading || trailingQuery.loading,
  };
}

// ── Suppression list (#631) ────────────────────────────────────────

interface AddResp {
  addEmailSuppressionEntry: MutationResult<{ address: string; reason: string }>;
}

interface RemoveResp {
  removeEmailSuppressionEntry: MutationResult<{
    address: string;
    removed: boolean;
  }>;
}

/** Add / remove manual suppression entries; refetches the detail on success. */
export function useSuppressionList(managedServiceId: string, onRefetch: () => void) {
  const [addEntry, { loading: adding }] = useMutation<AddResp>(ADD_EMAIL_SUPPRESSION_ENTRY, {
    refetchQueries: [
      {
        query: GET_EMAIL_SERVICE_DETAIL,
        variables: { managedServiceId },
      },
    ],
    awaitRefetchQueries: true,
  });

  const [removeEntry, { loading: removing }] = useMutation<RemoveResp>(
    REMOVE_EMAIL_SUPPRESSION_ENTRY,
    {
      refetchQueries: [
        {
          query: GET_EMAIL_SERVICE_DETAIL,
          variables: { managedServiceId },
        },
      ],
      awaitRefetchQueries: true,
    }
  );

  /** Resolves true when the address was suppressed (clear the form). */
  async function onAdd(address: string, note: string): Promise<boolean> {
    try {
      const { data } = await addEntry({
        variables: {
          input: {
            managedServiceId,
            address,
            reason: "MANUAL",
            note,
          },
        },
      });
      const env = data?.addEmailSuppressionEntry;
      if (!env) {
        toast.error("No response from server");
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Suppression add failed");
        return false;
      }
      toast.success(`Suppressed ${address}`);
      onRefetch();
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Suppression add failed");
      return false;
    }
  }

  /** Resolves true when the removal went through (close the dialog). */
  async function onRemove(entry: AstroliftEmailSuppressionEntry): Promise<boolean> {
    try {
      const { data } = await removeEntry({
        variables: {
          input: {
            managedServiceId,
            address: entry.address,
          },
        },
      });
      const env = data?.removeEmailSuppressionEntry;
      if (!env) {
        toast.error("No response from server");
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Suppression remove failed");
        return false;
      }
      if (env.data?.removed) {
        toast.success(`Removed ${entry.address} from suppression list`);
      } else {
        toast.info(`${entry.address} was already removed`);
      }
      onRefetch();
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Suppression remove failed");
      return false;
    }
  }

  return { adding, removing, onAdd, onRemove };
}

// ── Sender config (from-name, reply-to, env senders) (#637, #638) ───

interface UpdateManagedServiceResp {
  updateManagedService: MutationResult<{ id: string }>;
}

/** Saves the sender fields into the service config. */
export function useSenderConfig(managedServiceId: string, serviceConfig: Record<string, unknown>) {
  const [updateSvc, { loading }] = useMutation<UpdateManagedServiceResp>(UPDATE_MANAGED_SERVICE);

  /**
   * Writes `values` over the current config (blank values drop the key).
   * Resolves true when saved (leave edit mode).
   */
  async function onSave(values: Record<string, string>, keys: readonly string[]): Promise<boolean> {
    const nextConfig: Record<string, unknown> = { ...serviceConfig };
    for (const key of keys) {
      const v = (values[key] ?? "").trim();
      if (v) nextConfig[key] = v;
      else delete nextConfig[key];
    }
    try {
      const { data } = await updateSvc({
        variables: {
          input: { id: managedServiceId, config: nextConfig },
        },
      });
      const env = data?.updateManagedService;
      if (!env) {
        toast.error("No response from server");
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Update failed");
        return false;
      }
      toast.success("Sender config saved");
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
      return false;
    }
  }

  return { serviceConfig, saving: loading, onSave };
}

// ── Sender alert rules (#627, #639) ────────────────────────────────

export interface AlertRuleRow {
  id: string;
  name: string;
  target: string;
  targetId: string;
  managedServiceId: string | null;
  severity: string;
  predicate: Record<string, unknown>;
  notifyChannels: unknown;
  isActive: boolean;
  createdAt: string;
}

export type SesAlertKind = "ses_bounce_rate" | "ses_complaint_rate";

export interface NewAlertRule {
  kind: SesAlertKind;
  threshold: string;
  severity: string;
  name: string;
}

interface AlertRulesResp {
  astroliftAlertRules: AlertRuleRow[];
}

interface CreateAlertRuleResp {
  createAlertRule: MutationResult<AlertRuleRow>;
}

interface DeleteAlertRuleResp {
  deleteAlertRule: MutationResult<{ id: string; deleted: boolean }>;
}

export function defaultAlertName(kind: SesAlertKind, threshold: number): string {
  const label = kind === "ses_bounce_rate" ? "Bounce rate" : "Complaint rate";
  return `${label} > ${threshold}%`;
}

/** The service's alert rules, plus create and delete. */
export function useSenderAlertRules(managedServiceId: string) {
  const queryVars = React.useMemo(
    () => ({
      target: "managed_service",
      targetId: managedServiceId,
      activeOnly: false,
    }),
    [managedServiceId]
  );
  const { data, loading } = useQuery<AlertRulesResp>(LIST_ALERT_RULES, {
    variables: queryVars,
    fetchPolicy: "cache-and-network",
  });
  const refetchQueries = React.useMemo(
    () => [{ query: LIST_ALERT_RULES, variables: queryVars }],
    [queryVars]
  );

  const [createRule, { loading: creating }] = useMutation<CreateAlertRuleResp>(CREATE_ALERT_RULE, {
    refetchQueries,
    awaitRefetchQueries: true,
  });
  const [deleteRule, { loading: deleting }] = useMutation<DeleteAlertRuleResp>(DELETE_ALERT_RULE, {
    refetchQueries,
    awaitRefetchQueries: true,
  });

  /** Resolves true when the rule was created (clear the name field). */
  async function onCreate({ kind, threshold, severity, name }: NewAlertRule): Promise<boolean> {
    const n = Number(threshold);
    if (!Number.isFinite(n) || n <= 0) {
      toast.error("Threshold must be a positive number");
      return false;
    }
    const ruleName = name.trim() || defaultAlertName(kind, n);
    try {
      const { data } = await createRule({
        variables: {
          input: {
            name: ruleName,
            target: "managed_service",
            targetId: managedServiceId,
            managedServiceId,
            severity,
            predicate: { kind, threshold_pct: n },
            notifyChannels: [{ kind: "in_app", ref: "" }],
            isActive: true,
          },
        },
      });
      const env = data?.createAlertRule;
      if (!env) {
        toast.error("No response from server");
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Create alert failed");
        return false;
      }
      toast.success(`Created ${ruleName}`);
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Create alert failed");
      return false;
    }
  }

  async function onDelete(rule: AlertRuleRow): Promise<void> {
    try {
      const { data } = await deleteRule({
        variables: { input: { id: rule.id } },
      });
      const env = data?.deleteAlertRule;
      if (!env) return;
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Delete alert failed");
        return;
      }
      toast.success(`Deleted ${rule.name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete alert failed");
    }
  }

  return {
    rules: data?.astroliftAlertRules ?? [],
    loading,
    creating,
    deleting,
    onCreate,
    onDelete,
  };
}

// ── Engagement metrics (#626) ──────────────────────────────────────

interface EngagementResp {
  astroliftEmailEngagementMetrics: AstroliftEmailEngagementMetrics | null;
}

/** 30-day open / click / bounce / complaint rates. */
export function useEngagementMetrics(managedServiceId: string) {
  const { data, loading } = useQuery<EngagementResp>(GET_EMAIL_ENGAGEMENT_METRICS, {
    variables: { managedServiceId, days: 30 },
    fetchPolicy: "cache-and-network",
  });
  return { metrics: data?.astroliftEmailEngagementMetrics ?? null, loading };
}

// ── Message log + bounce/complaint drill-in (#624, #625) ───────────

interface MessagesResp {
  astroliftEmailMessages: AstroliftEmailMessage[];
}

/**
 * The recent-message log. The event filter and the applied recipient
 * filter are query variables, so they live here; the recipient draft
 * being typed lives in the view.
 */
export function useMessageLog(managedServiceId: string) {
  const [eventKind, setEventKind] = React.useState<string>("");
  const [appliedRecipient, setAppliedRecipient] = React.useState<string>("");

  const { data, loading, refetch } = useQuery<MessagesResp>(GET_EMAIL_MESSAGES, {
    variables: {
      managedServiceId,
      limit: 50,
      eventKind: eventKind || null,
      recipient: appliedRecipient || null,
    },
    fetchPolicy: "cache-and-network",
  });

  return {
    messages: data?.astroliftEmailMessages ?? [],
    loading,
    onRefresh: () => {
      void refetch();
    },
    eventKind,
    onEventKindChange: setEventKind,
    onApplyRecipient: setAppliedRecipient,
  };
}

// ── Template management (#635, #628) ───────────────────────────────

export interface TemplateDraft {
  name: string;
  subject: string;
  htmlBody: string;
  textBody: string;
}

interface TemplatesResp {
  astroliftEmailTemplates: AstroliftEmailTemplate[];
}

interface TemplateStatsResp {
  astroliftEmailTemplateStats: AstroliftTemplateSendStatPoint[];
}

interface TemplateMutationResp {
  createEmailTemplate?: MutationResult<AstroliftEmailTemplate>;
  updateEmailTemplate?: MutationResult<AstroliftEmailTemplate>;
}

interface DeleteTemplateResp {
  deleteEmailTemplate: MutationResult<{ id: string; deleted: boolean }>;
}

/** The service's SES templates, plus create / update / delete. */
export function useEmailTemplates(managedServiceId: string) {
  const queryVars = React.useMemo(() => ({ managedServiceId }), [managedServiceId]);
  const { data, loading } = useQuery<TemplatesResp>(GET_EMAIL_TEMPLATES, {
    variables: queryVars,
    fetchPolicy: "cache-and-network",
  });
  const refetchQueries = React.useMemo(
    () => [{ query: GET_EMAIL_TEMPLATES, variables: queryVars }],
    [queryVars]
  );

  const [createTemplate, { loading: creating }] = useMutation<TemplateMutationResp>(
    CREATE_EMAIL_TEMPLATE,
    {
      refetchQueries,
      awaitRefetchQueries: true,
    }
  );
  const [updateTemplate, { loading: updating }] = useMutation<TemplateMutationResp>(
    UPDATE_EMAIL_TEMPLATE,
    {
      refetchQueries,
      awaitRefetchQueries: true,
    }
  );
  const [deleteTemplate, { loading: deleting }] = useMutation<DeleteTemplateResp>(
    DELETE_EMAIL_TEMPLATE,
    {
      refetchQueries,
      awaitRefetchQueries: true,
    }
  );

  /**
   * Creates the template when `editingName` is null, otherwise updates
   * `editingName`. Resolves true when saved (close the form).
   */
  async function onSave(draft: TemplateDraft, editingName: string | null): Promise<boolean> {
    const name = draft.name.trim();
    const subject = draft.subject.trim();
    if (!name) {
      toast.error("Template name required");
      return false;
    }
    if (!subject) {
      toast.error("Subject required");
      return false;
    }
    try {
      if (editingName === null) {
        const { data } = await createTemplate({
          variables: {
            input: {
              managedServiceId,
              name,
              subject,
              htmlBody: draft.htmlBody,
              textBody: draft.textBody,
            },
          },
        });
        const env = data?.createEmailTemplate;
        if (!env) {
          toast.error("No response from server");
          return false;
        }
        if (!env.ok) {
          toast.error(env.errors[0]?.message ?? "Create template failed");
          return false;
        }
        toast.success(`Created template ${name}`);
      } else {
        const { data } = await updateTemplate({
          variables: {
            input: {
              managedServiceId,
              name: editingName,
              subject,
              htmlBody: draft.htmlBody,
              textBody: draft.textBody,
            },
          },
        });
        const env = data?.updateEmailTemplate;
        if (!env) {
          toast.error("No response from server");
          return false;
        }
        if (!env.ok) {
          toast.error(env.errors[0]?.message ?? "Update template failed");
          return false;
        }
        toast.success(`Updated template ${editingName}`);
      }
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
      return false;
    }
  }

  /** Resolves true when the template was deleted (close the dialog). */
  async function onDelete(template: AstroliftEmailTemplate): Promise<boolean> {
    try {
      const { data } = await deleteTemplate({
        variables: {
          input: { managedServiceId, name: template.name },
        },
      });
      const env = data?.deleteEmailTemplate;
      if (!env) return false;
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Delete failed");
        return false;
      }
      toast.success(`Deleted ${template.name}`);
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
      return false;
    }
  }

  return {
    templates: data?.astroliftEmailTemplates ?? [],
    loading,
    saving: creating || updating,
    deleting,
    onSave,
    onDelete,
  };
}

/** One template's 14-day send series, fetched while its stats row is open. */
export function useTemplateStats(managedServiceId: string, name: string) {
  const { data, loading } = useQuery<TemplateStatsResp>(GET_EMAIL_TEMPLATE_STATS, {
    variables: { managedServiceId, name, days: 14 },
    fetchPolicy: "cache-and-network",
  });
  return { name, points: data?.astroliftEmailTemplateStats ?? [], loading };
}
