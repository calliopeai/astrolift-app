"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useCursorFeed } from "@/components/feed/use-cursor-feed";
import { useListState } from "@/components/list/use-list-state";
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
import { useMe } from "@/graphql/user/user.hooks";

import type { AuditLogScreenProps } from "./AuditLogScreen";
import { AUDIT_LIST, auditVariables, matchesTargetKind } from "./audit-list";

interface PageResp {
  astroliftAuditEventsPage: AstroliftAuditEventPage;
}

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
 * The audit trail's data half: the event feed (views, search and chips in
 * the URL, spec 44 §5.1; older events load on the server's cursor as the
 * reader nears the end, list rule 5), the retention window, the server-side
 * export of the filter set in view, and the retention editor's write
 * (through `updateOrganization`, the same field the
 * /administration/organization settings page edits).
 */
export function useAuditLog(): AuditLogScreenProps {
  const t = useTranslations("lists.audit");
  const { org } = useActiveOrg();
  const { user } = useMe();
  const list = useListState(AUDIT_LIST);
  const [exporting, setExporting] = React.useState(false);
  // `since:24h` is anchored when the page opens, so the variables stay
  // stable across renders; the 5s poll still brings in newer events.
  const [now] = React.useState(() => Date.now());

  const { variables, ready } = auditVariables(list.filters, list.state.q, {
    pageSize: list.state.pageSize,
    after: null,
    viewerId: user?.id ?? null,
    now,
  });
  // The feed owns the page size and the cursor.
  const { limit: _limit, after: _after, ...question } = variables;

  const { feed, totalCount } = useCursorFeed<PageResp, AstroliftAuditEvent>(
    LIST_AUDIT_EVENTS_PAGE,
    {
      variables: question,
      select: (d) => d?.astroliftAuditEventsPage,
      keyOf: (r) => r.id,
      pageSize: list.state.pageSize,
      skip: !ready,
      pollInterval: 5000,
    }
  );

  const targetKind = list.filters.target;
  const items = React.useMemo(
    () => feed.items.filter((r) => matchesTargetKind(r, targetKind)),
    [feed.items, targetKind]
  );

  const { data: retentionData } = useQuery<RetentionResp>(GET_AUDIT_RETENTION, {
    fetchPolicy: "cache-first",
  });

  const [exportMutation] = useMutation<ExportResp>(EXPORT_AUDIT_EVENTS);

  // The export takes the filter set the operator is looking at, not the page.
  const onExport = React.useCallback(
    async (format: AuditExportFormat) => {
      setExporting(true);
      const toastId = toast.loading(t("export.toastStart"));
      try {
        const result = await exportMutation({
          variables: {
            input: {
              format: format.toUpperCase(),
              action: variables.action,
              decision: variables.decision,
              actorId: variables.actorId,
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
    [
      exportMutation,
      t,
      variables.action,
      variables.decision,
      variables.actorId,
      variables.createdAtGte,
      variables.createdAtLte,
    ]
  );

  const [updateOrg, { loading: savingRetention }] = useMutation<{
    updateOrganization: MutationResult<AstroliftOrganization>;
  }>(UPDATE_ORGANIZATION, {
    refetchQueries: [{ query: GET_AUDIT_RETENTION }],
    awaitRefetchQueries: true,
  });

  /**
   * Writes the retention window. The server enforces ORG_UPDATE and the
   * 1..2557 range; the retention query is refetched so the header updates
   * in place. Resolves true when saved, so the dialog can close.
   */
  const saveRetention = React.useCallback(
    async (days: number): Promise<boolean> => {
      if (!org) return false;
      try {
        const { data: saved } = await updateOrg({
          variables: { input: { id: org.id, auditLogRetentionDays: days } },
        });
        const payload = saved?.updateOrganization;
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
    list,
    events: { ...feed, items, loading: feed.loading || !ready },
    // A client-side target filter makes the server's count wrong for the rows shown.
    totalCount: targetKind ? null : totalCount,
    targetFilteredLocally: Boolean(targetKind),
    retentionDays: retentionData?.astroliftAuditRetention?.days ?? null,
    exporting,
    onExport,
    canEditRetention: org != null,
    savingRetention,
    saveRetention,
  };
}
