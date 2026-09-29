"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { EXPORT_ASTROLIFT_APP_LOGS } from "@/graphql/operations/operations.mutations";
import type {
  AppLogExportFormat,
  AstroliftAppLogExport,
} from "@/graphql/operations/operations.types";

import type { LogExportRequest } from "./AppLogExportDialog";

interface ExportResp {
  exportAstroliftAppLogs: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field: string | null }>;
    data: AstroliftAppLogExport | null;
  };
}

/**
 * Convert a ``yyyy-mm-ddThh:mm`` datetime-local input value to an
 * ISO 8601 timestamp (UTC). Empty strings return null so the field
 * is dropped from the mutation variables.
 */
function localToIso(value: string): string | null {
  if (!value) return null;
  const ts = new Date(value);
  if (Number.isNaN(ts.getTime())) return null;
  return ts.toISOString();
}

/**
 * Runs a log export for one pod and reports it as a toast with a download
 * action. The data half of AppLogExportDialog: ``onExport`` resolves true
 * when the export is ready, so the dialog can close.
 */
export function useLogExport(scope: {
  appSlug: string;
  podName: string | null;
  container: string | null;
  environmentName?: string | null;
  workloadSlug?: string | null;
}) {
  const t = useTranslations("apps.observability.logExport");
  const [busy, setBusy] = React.useState(false);
  const [exportMutation] = useMutation<ExportResp>(EXPORT_ASTROLIFT_APP_LOGS);
  const { appSlug, podName, container, environmentName, workloadSlug } = scope;

  const onExport = React.useCallback(
    async ({ format, since, until, level, regex }: LogExportRequest): Promise<boolean> => {
      if (!podName) {
        toast.error(t("missingPod"));
        return false;
      }
      setBusy(true);
      const toastId = toast.loading(t("toastStart"));
      try {
        const result = await exportMutation({
          variables: {
            input: {
              appSlug,
              podName,
              container: container ?? null,
              environmentName: environmentName ?? null,
              workloadSlug: workloadSlug ?? null,
              format,
              since: localToIso(since),
              until: localToIso(until),
              level: level ? level : null,
              regex: regex ? regex : null,
            },
          },
        });
        const payload = result.data?.exportAstroliftAppLogs;
        if (!payload?.ok || !payload.data) {
          const msg = payload?.errors?.[0]?.message ?? t("toastFailure");
          toast.error(msg, { id: toastId });
          return false;
        }
        const fmtLabel = (payload.data.format as AppLogExportFormat).toUpperCase();
        const ready = payload.data.truncated
          ? t("toastReadyTruncated", { rows: payload.data.rowCount, format: fmtLabel })
          : t("toastReady", { rows: payload.data.rowCount, format: fmtLabel });
        toast.success(ready, {
          id: toastId,
          action: {
            label: t("toastDownload"),
            onClick: () => {
              window.open(payload.data!.downloadUrl, "_blank", "noopener,noreferrer");
            },
          },
          duration: 30000,
        });
        return true;
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("toastFailure"), {
          id: toastId,
        });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [appSlug, container, environmentName, exportMutation, podName, t, workloadSlug]
  );

  return { busy, onExport };
}
