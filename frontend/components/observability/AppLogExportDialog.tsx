"use client";

/**
 * AppLogExportDialog — modal that fires the ``exportAstroliftAppLogs``
 * mutation (#483) and surfaces a toast with a download link on
 * success.
 *
 * Mirrors the audit-log export's UX (#433): one shot mutation, no
 * polling — the backend renders the file synchronously and returns
 * the signed URL inline. Token + TTL live on the server; this
 * component just hands the URL to the operator.
 *
 * The form keeps every filter optional so a mobile-friendly default
 * (last hour, raw text) is one click. Time-range pickers reuse the
 * date input pattern the audit page already established.
 */

import { useMutation } from "@apollo/client/react";
import { DownloadIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EXPORT_ASTROLIFT_APP_LOGS } from "@/graphql/operations/operations.mutations";
import type {
  AppLogExportFormat,
  AstroliftAppLogExport,
} from "@/graphql/operations/operations.types";

type GqlFormat = "CSV" | "NDJSON" | "TXT";

interface ExportResp {
  exportAstroliftAppLogs: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field: string | null }>;
    data: AstroliftAppLogExport | null;
  };
}

export interface AppLogExportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  appSlug: string;
  podName: string | null;
  container: string | null;
  environmentName?: string | null;
  workloadSlug?: string | null;
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

export function AppLogExportDialog({
  open,
  onOpenChange,
  appSlug,
  podName,
  container,
  environmentName,
  workloadSlug,
}: AppLogExportDialogProps) {
  const t = useTranslations("apps.observability.logExport");
  const tCommon = useTranslations("apps.common");

  const [format, setFormat] = React.useState<GqlFormat>("TXT");
  const [since, setSince] = React.useState("");
  const [until, setUntil] = React.useState("");
  const [level, setLevel] = React.useState<string>("");
  const [regex, setRegex] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const [exportMutation] = useMutation<ExportResp>(EXPORT_ASTROLIFT_APP_LOGS);

  const podLocked = !podName;

  const handleSubmit = React.useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (!podName) {
        toast.error(t("missingPod"));
        return;
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
          return;
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
        onOpenChange(false);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("toastFailure"), {
          id: toastId,
        });
      } finally {
        setBusy(false);
      }
    },
    [
      appSlug,
      container,
      environmentName,
      exportMutation,
      format,
      level,
      onOpenChange,
      podName,
      regex,
      since,
      t,
      until,
      workloadSlug,
    ]
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t("title")}</DialogTitle>
            <DialogDescription>
              {podLocked ? t("descriptionNoPod") : t("description", { pod: podName ?? "" })}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="alx-format">{t("formatLabel")}</Label>
              <Select
                value={format}
                onValueChange={(v) => setFormat(v as GqlFormat)}
                disabled={busy}
              >
                <SelectTrigger id="alx-format">
                  <SelectValue placeholder={t("formatPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="TXT">{t("formatTxt")}</SelectItem>
                  <SelectItem value="NDJSON">{t("formatNdjson")}</SelectItem>
                  <SelectItem value="CSV">{t("formatCsv")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="alx-since">{t("sinceLabel")}</Label>
                <Input
                  id="alx-since"
                  type="datetime-local"
                  value={since}
                  onChange={(e) => setSince(e.target.value)}
                  disabled={busy}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="alx-until">{t("untilLabel")}</Label>
                <Input
                  id="alx-until"
                  type="datetime-local"
                  value={until}
                  onChange={(e) => setUntil(e.target.value)}
                  disabled={busy}
                />
              </div>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="alx-level">{t("levelLabel")}</Label>
              <Input
                id="alx-level"
                placeholder={t("levelPlaceholder")}
                value={level}
                onChange={(e) => setLevel(e.target.value)}
                disabled={busy}
              />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="alx-regex">{t("regexLabel")}</Label>
              <Input
                id="alx-regex"
                placeholder={t("regexPlaceholder")}
                value={regex}
                onChange={(e) => setRegex(e.target.value)}
                disabled={busy}
              />
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={busy}
            >
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !podName}>
              {busy ? (
                <>
                  <Loader2Icon className="size-3 animate-spin" />
                  {t("submittingButton")}
                </>
              ) : (
                <>
                  <DownloadIcon className="size-3" />
                  {t("submitButton")}
                </>
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
