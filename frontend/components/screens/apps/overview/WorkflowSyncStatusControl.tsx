"use client";

import {
  DownloadIcon,
  GitPullRequestIcon,
  Loader2Icon,
  RefreshCwIcon,
  UploadCloudIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import type { AstroliftCiWorkflowSyncStatus } from "@/graphql/__generated__/schema";
import { DRIFT_BADGE, formatRelativeWebhookInstall } from "./ci-setup-meta";
import type { CiSetupData } from "./use-ci-setup";

export function WorkflowSyncStatusControl({
  status,
  pushing,
  pulling,
  refreshing,
  reconciling,
  onRefresh,
  onPush,
  onReconcile,
  onPull,
}: { status: AstroliftCiWorkflowSyncStatus } & CiSetupData["workflowSync"]) {
  const t = useTranslations("apps.overview.ciWorkflow");
  const relative = (iso: string) =>
    formatRelativeWebhookInstall(iso, (key, values) => t(`relative.${key}`, values));
  const [confirmPullOpen, setConfirmPullOpen] = React.useState(false);
  const [comparing, setComparing] = React.useState(false);

  const known = Object.hasOwn(DRIFT_BADGE, status.state);
  const badge = known ? DRIFT_BADGE[status.state] : DRIFT_BADGE.unknown;
  const label = known ? t(`states.${status.state}.label`) : status.state;
  const hint = t(`states.${known ? status.state : "unknown"}.hint`);
  const busy = pushing || pulling || refreshing || reconciling;
  // A reconcile PR only makes sense for a drifted (hand-edited) file.
  const canReconcile = status.state === "repo_drift" || status.state === "conflict";

  async function handlePull() {
    await onPull();
    setComparing(true);
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-dashed p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium">{t("title")}</p>
            <span
              className={
                "text-2xs inline-flex max-w-full min-w-0 items-center gap-1 rounded-full px-2 py-0.5 font-medium [overflow-wrap:anywhere] " +
                badge.className
              }
            >
              {label}
            </span>
            {status.syncedTemplateVersion != null &&
            status.syncedTemplateVersion !== status.currentTemplateVersion ? (
              <span className="text-muted-foreground text-2xs">
                {t("templateVersions", {
                  previous: status.syncedTemplateVersion,
                  current: status.currentTemplateVersion,
                })}
              </span>
            ) : null}
          </div>
          <p className="text-muted-foreground mt-0.5 text-xs">{hint}</p>
          {status.checkedAt ? (
            <p className="text-muted-foreground text-2xs mt-0.5">
              {t("lastChecked", { relative: relative(status.checkedAt) })}
            </p>
          ) : null}
        </div>
      </div>

      <Can permission="app.update">
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={onRefresh}
            disabled={busy}
            className="gap-1.5"
          >
            {refreshing ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            {t("check")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setConfirmPullOpen(true)}
            disabled={busy}
            className="gap-1.5"
          >
            {pulling ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <DownloadIcon className="size-3.5" />
            )}
            {t("pull")}
          </Button>
          <Button size="sm" variant="outline" onClick={onPush} disabled={busy} className="gap-1.5">
            {pushing ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <UploadCloudIcon className="size-3.5" />
            )}
            {t("push")}
          </Button>
          {canReconcile ? (
            <Button
              size="sm"
              variant="outline"
              onClick={onReconcile}
              disabled={busy}
              className="gap-1.5"
            >
              {reconciling ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <GitPullRequestIcon className="size-3.5" />
              )}
              {t("reconcile")}
            </Button>
          ) : null}
        </div>
      </Can>

      {status.repoText && status.renderedText && status.repoText !== status.renderedText ? (
        <div className="border-border rounded-md border">
          <button
            type="button"
            onClick={() => setComparing((v) => !v)}
            className="text-muted-foreground hover:text-foreground flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs"
          >
            <span>
              {status.repoTextPulledAt
                ? t("comparePulled", { relative: relative(status.repoTextPulledAt) })
                : t("compare")}
            </span>
            <span aria-hidden>{comparing ? "−" : "+"}</span>
          </button>
          {comparing ? (
            <div className="grid gap-3 border-t p-3 md:grid-cols-2">
              <div className="min-w-0">
                <p className="text-muted-foreground text-2xs mb-1 font-medium uppercase">
                  {t("repoVersion")}
                </p>
                <pre className="bg-muted/40 max-h-72 overflow-auto rounded-sm p-2 text-xs leading-relaxed">
                  {status.repoText}
                </pre>
              </div>
              <div className="min-w-0">
                <p className="text-muted-foreground text-2xs mb-1 font-medium uppercase">
                  {t("pushVersion")}
                </p>
                <pre className="bg-muted/40 max-h-72 overflow-auto rounded-sm p-2 text-xs leading-relaxed">
                  {status.renderedText}
                </pre>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <ConfirmDialog
        open={confirmPullOpen}
        onOpenChange={setConfirmPullOpen}
        title={t("pullTitle")}
        description={t("pullDescription")}
        confirmLabel={t("pull")}
        onConfirm={handlePull}
      />
    </div>
  );
}
