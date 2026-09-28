"use client";

import { Loader2Icon, PauseIcon, PlayIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Textarea } from "@/components/ui/textarea";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useWebhookDeploysPause } from "./use-webhook-deploys-pause";

export type WebhookDeploysPauseViewProps = ReturnType<typeof useWebhookDeploysPause> & {
  paused: boolean;
  pausedAt: string | null;
  pausedByEmail: string | null;
  pauseReason: string;
};

/**
 * App-global webhook-deploy pause toggle (#399).
 *
 * Independent of the per-environment `deploys_paused` (#378) and
 * `ingress_paused` axes. Stops the deploy storm from CI / push /
 * scheduled triggers across every environment of this app without
 * paging through each env's controls and without taking ingress down.
 * Manual deploys from the UI / CLI continue to flow — explicit
 * on-call escape valve so a wedged CI can be stopped without locking
 * the operator out of fixing the app.
 *
 * UX shape mirrors the IngressRow pattern:
 * - Live pill (emerald play) ↔ Paused pill (amber pause).
 * - Toggle on OFF→ON opens a confirm dialog with an optional reason
 *   textarea — the reason lands on the audit log AND the row, surfaced
 *   below as "Paused by X · 5m ago — reason: <reason>".
 * - OFF→ON only — resuming is one click (the inverse always works).
 * - Hidden behind `app.deploy` for read-only viewers.
 */
export function WebhookDeploysPauseView({
  pausing,
  resuming,
  onPause,
  onResume,
  paused,
  pausedAt,
  pausedByEmail,
  pauseReason,
}: WebhookDeploysPauseViewProps) {
  const t = useTranslations("apps.settings.webhookDeploys");
  const fmt = useFormatters();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");
  const busy = pausing || resuming;

  React.useEffect(() => {
    if (!confirmOpen) setReason("");
  }, [confirmOpen]);

  async function handlePauseConfirm() {
    if (await onPause(reason)) setConfirmOpen(false);
  }

  return (
    <Section
      title={t("title")}
      description={t("description")}
      action={
        <>
          {paused ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              <PauseIcon className="size-3" />
              {t("paused")}
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-success-border bg-success/10 text-success-fg"
            >
              <PlayIcon className="size-3" />
              {t("live")}
            </Badge>
          )}
          <Can permission="app.deploy">
            {paused ? (
              <Button size="sm" variant="default" onClick={onResume} disabled={busy}>
                {busy ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <PlayIcon className="size-3.5" />
                )}
                {t("resume")}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setConfirmOpen(true)}
                disabled={busy}
              >
                {busy ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <PauseIcon className="size-3.5" />
                )}
                {t("pause")}
              </Button>
            )}
          </Can>
        </>
      }
    >
      {/* Audit / explainer footer — the paused branch surfaces the
          actor + time + reason so the operator can spot a stale pause
          at a glance; the live branch reminds operators what the
          toggle's scope is so the next "why is CI not deploying?"
          page lands here first. */}
      {paused ? (
        <div className="space-y-1.5 text-xs">
          <p className="text-foreground">
            {t.rich("pausedBy", {
              who: () => (
                <span className="text-foreground font-medium">
                  {pausedByEmail ?? t("unknownActor")}
                </span>
              ),
              when: () => (
                <span className="text-muted-foreground">
                  {pausedAt ? fmt.formatRelativeTime(pausedAt) : t("unknownTime")}
                </span>
              ),
            })}
          </p>
          {pauseReason ? (
            <p className="text-muted-foreground">
              {t("reasonPrefix")} <span className="text-foreground">{pauseReason}</span>
            </p>
          ) : (
            <p className="text-muted-foreground italic">{t("noReason")}</p>
          )}
          <p className="text-muted-foreground text-2xs">{t("scopeNotePaused")}</p>
        </div>
      ) : (
        <p className="text-muted-foreground text-2xs">{t("scopeNoteLive")}</p>
      )}

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <PauseIcon className="text-warning-fg size-4" />
              {t("confirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("confirmDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="grid gap-2 py-2">
            <Label htmlFor="webhook-pause-reason" className="text-xs">
              {t("reasonLabel")}
            </Label>
            <Textarea
              id="webhook-pause-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("reasonPlaceholder")}
              rows={3}
              maxLength={512}
            />
            <p className="text-muted-foreground text-2xs">{t("reasonHelp")}</p>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pausing}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handlePauseConfirm();
              }}
              disabled={pausing}
              className="bg-amber-600 text-white hover:bg-amber-700"
            >
              {pausing ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {t("confirmButton")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}
