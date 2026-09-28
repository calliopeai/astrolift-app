"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable } from "@/components/data-table";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { UPDATE_ORGANIZATION } from "@/graphql/identity/identity.mutations";
import type { AstroliftOrganization, MutationResult } from "@/graphql/identity/identity.types";
import { EXPORT_AUDIT_EVENTS } from "@/graphql/operations/operations.mutations";
import {
  GET_AUDIT_RETENTION,
  LIST_AUDIT_EVENTS_PAGE,
} from "@/graphql/operations/operations.queries";
import type {
  AstroliftAuditEvent,
  AstroliftAuditEventPage,
  AstroliftAuditExport,
  AstroliftAuditRetention,
  AuditExportFormat,
} from "@/graphql/operations/operations.types";

interface PageResp {
  astroliftAuditEventsPage: AstroliftAuditEventPage;
}

/**
 * The audit trail is the densest list on the platform and operators
 * read it a screen at a time, so this surface keeps the 100-row page
 * the hand-rolled version defaulted to rather than DataTable's 25.
 */
const AUDIT_PAGE_SIZE = 100;

interface RetentionResp {
  astroliftAuditRetention: AstroliftAuditRetention;
}

interface ExportResp {
  exportAuditEvents: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field: string | null }>;
    data: AstroliftAuditExport | null;
  };
}

/**
 * Convert a yyyy-mm-dd input value to an ISO 8601 timestamp at UTC
 * midnight. Returns null for empty strings so the variable is dropped
 * from the query. The DateTime scalar on the server accepts ISO 8601.
 */
function dateInputToIso(value: string, endOfDay = false): string | null {
  if (!value) return null;
  const [y, m, d] = value.split("-").map((s) => Number.parseInt(s, 10));
  if (!y || !m || !d) return null;
  const ts = endOfDay ? Date.UTC(y, m - 1, d, 23, 59, 59, 999) : Date.UTC(y, m - 1, d, 0, 0, 0, 0);
  return new Date(ts).toISOString();
}

/**
 * The audit trail's data half: the cursor-paged event table and its
 * filters, the retention window, the export, and the retention editor's
 * write (through `updateOrganization`, the same field the
 * /administration/organization settings page edits).
 */
export function useAuditLog() {
  const t = useTranslations("lists.audit");
  const { org } = useActiveOrg();

  const [decisionFilter, setDecisionFilter] = React.useState<string>("");
  const [fromDate, setFromDate] = React.useState<string>("");
  const [toDate, setToDate] = React.useState<string>("");
  const [exporting, setExporting] = React.useState(false);

  // Filters the controller doesn't own. Changing any of them resets the
  // cursor walk to page one, which is what a different result set needs.
  const variables = React.useMemo(
    () => ({
      decision: decisionFilter || null,
      createdAtGte: dateInputToIso(fromDate, false),
      createdAtLte: dateInputToIso(toDate, true),
      // `totalCount` on this page is opt-in — a full-range count is
      // expensive, so the caller asks for it.
      includeTotal: true,
    }),
    [decisionFilter, fromDate, toDate]
  );

  const table = useCursorTable<AstroliftAuditEvent>({
    query: LIST_AUDIT_EVENTS_PAGE,
    variables,
    extract: (d) => (d as PageResp | undefined)?.astroliftAuditEventsPage,
    // The action box is the query's `action` argument, debounced by the
    // controller. It used to be an ordinary <Input> wired straight into
    // the query variables, which cost one network round trip per
    // keystroke. Note the server matches it *exactly*, hence the
    // placeholder and the filtered-empty copy on the screen.
    searchVariable: "action",
    pageSize: AUDIT_PAGE_SIZE,
    urlKey: "audit",
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });

  const { data: retentionData } = useQuery<RetentionResp>(GET_AUDIT_RETENTION, {
    fetchPolicy: "cache-first",
  });

  const [exportMutation] = useMutation<ExportResp>(EXPORT_AUDIT_EVENTS);

  // The export takes the filter set the operator is looking at, not the
  // page: `action` comes from the search box, the rest from `variables`.
  const actionFilter = table.search.trim();

  const onExport = React.useCallback(
    async (format: AuditExportFormat) => {
      setExporting(true);
      const toastId = toast.loading(t("export.toastStart"));
      try {
        const result = await exportMutation({
          variables: {
            input: {
              format: format.toUpperCase(),
              action: actionFilter || null,
              decision: variables.decision,
              actorId: null,
              createdAtGte: variables.createdAtGte,
              createdAtLte: variables.createdAtLte,
            },
          },
        });
        const payload = result.data?.exportAuditEvents;
        if (!payload?.ok || !payload.data) {
          const msg = payload?.errors?.[0]?.message ?? t("export.toastFailure");
          toast.error(msg, { id: toastId });
          return;
        }
        toast.success(t("export.toastReady", { rows: payload.data.rowCount }), {
          id: toastId,
          action: {
            label: t("export.toastDownload"),
            onClick: () => {
              window.open(payload.data!.downloadUrl, "_blank", "noopener,noreferrer");
            },
          },
          duration: 30000,
        });
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("export.toastFailure"), {
          id: toastId,
        });
      } finally {
        setExporting(false);
      }
    },
    [exportMutation, t, actionFilter, variables]
  );

  const [updateOrg, { loading: savingRetention }] = useMutation<{
    updateOrganization: MutationResult<AstroliftOrganization>;
  }>(UPDATE_ORGANIZATION, {
    refetchQueries: [{ query: GET_AUDIT_RETENTION }],
    awaitRefetchQueries: true,
  });

  /**
   * Writes the retention window. The server enforces ORG_UPDATE and the
   * 1..2557 range; the retention query is refetched so the subtitle
   * updates in place. Resolves true when saved, so the dialog can close.
   */
  const saveRetention = React.useCallback(
    async (days: number): Promise<boolean> => {
      if (!org) return false;
      try {
        const { data } = await updateOrg({
          variables: { input: { id: org.id, auditLogRetentionDays: days } },
        });
        const payload = data?.updateOrganization;
        if (payload?.ok) {
          toast.success(t("retention.toastSuccess", { days }));
          return true;
        }
        toast.error(payload?.errors?.[0]?.message ?? t("retention.toastFailure"));
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("retention.toastFailure"));
      }
      return false;
    },
    [org, updateOrg, t]
  );

  return {
    table,
    decisionFilter,
    onDecisionFilterChange: setDecisionFilter,
    fromDate,
    onFromDateChange: setFromDate,
    toDate,
    onToDateChange: setToDate,
    retentionDays: retentionData?.astroliftAuditRetention?.days ?? null,
    exporting,
    onExport,
    canEditRetention: org != null,
    savingRetention,
    saveRetention,
  };
}
