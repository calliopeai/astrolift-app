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

import { DownloadIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

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
type GqlFormat = "CSV" | "NDJSON" | "TXT";

/** What the operator asked for; datetimes are ``datetime-local`` values. */
export interface LogExportRequest {
  format: GqlFormat;
  since: string;
  until: string;
  level: string;
  regex: string;
}

export interface AppLogExportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The pod being exported; null until one is selected. */
  podName: string | null;
  busy: boolean;
  /** Resolves true once the export is ready; the dialog then closes. */
  onExport: (request: LogExportRequest) => Promise<boolean>;
}

export function AppLogExportDialog({
  open,
  onOpenChange,
  podName,
  busy,
  onExport,
}: AppLogExportDialogProps) {
  const t = useTranslations("apps.observability.logExport");
  const tCommon = useTranslations("apps.common");

  const [format, setFormat] = React.useState<GqlFormat>("TXT");
  const [since, setSince] = React.useState("");
  const [until, setUntil] = React.useState("");
  const [level, setLevel] = React.useState<string>("");
  const [regex, setRegex] = React.useState("");

  const podLocked = !podName;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (await onExport({ format, since, until, level, regex })) onOpenChange(false);
  };

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
