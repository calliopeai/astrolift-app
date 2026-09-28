"use client";

/**
 * ReprovisionCalloutView (#407 A) — state-specific banner above the deploy
 * activity strip on the app overview.
 *
 * Renders only when the backend's `astroliftApp.reprovision.needsReprovision`
 * is true. State copy + tone keyed on `reprovision.state`:
 *
 * - `failed` (red): "Provisioning failed — retry to rebuild."
 * - `pending` (amber): "Provisioning hasn't started yet — kick it off."
 * - `provisioning` (amber, no CTA): "Provisioning in progress (started Xm ago)."
 * - `ready_missing_registry` (amber): "Ready but ECR repo missing — re-provision to fix."
 *
 * The CTA fires `FORCE_REDEPLOY` with `confirmSlug = app.slug`. The
 * mutation is the existing recovery path (#389) — it cancels in-flight
 * deploys, deletes orphan k8s objects, and re-dispatches the deploy
 * workflow. We render a confirm dialog before firing so a stray click
 * never tears live traffic on an app the operator only meant to view.
 */

import { AlertTriangleIcon, Loader2Icon, RotateCwIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import type { AstroliftAppReprovisionState } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import type { useForceReprovision } from "./use-force-reprovision";

export type ReprovisionCalloutViewProps = ReturnType<typeof useForceReprovision> & {
  reprovision: AstroliftAppReprovisionState;
};

const TONE_BY_STATE: Record<string, "error" | "warn"> = {
  failed: "error",
  pending: "warn",
  provisioning: "warn",
  ready_missing_registry: "warn",
};

export function ReprovisionCalloutView({
  appSlug,
  reprovision,
  redeploying,
  onConfirm,
}: ReprovisionCalloutViewProps) {
  const t = useTranslations("apps.detail.reprovision");
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  if (!reprovision.needsReprovision) return null;

  const tone = TONE_BY_STATE[reprovision.state] ?? "warn";
  const isInFlight = reprovision.state === "provisioning";

  // Elapsed-time copy: only meaningful when the state is in-flight or
  // we want operators to see "started Xm ago" so they know whether to
  // wait or to act. Backend gives us seconds; we render whole minutes.
  const elapsedMinutes =
    reprovision.elapsedSeconds != null
      ? Math.max(0, Math.floor(reprovision.elapsedSeconds / 60))
      : null;

  const headline = t(`headline.${reprovision.state}`, {
    defaultValue: t("headline.unknown"),
  });
  const description =
    reprovision.reason ||
    t(`description.${reprovision.state}`, {
      defaultValue: t("description.unknown"),
    });
  const elapsedLabel = elapsedMinutes != null ? t("elapsed", { minutes: elapsedMinutes }) : "";

  async function handleConfirm() {
    await onConfirm();
    setConfirmOpen(false);
  }

  return (
    <>
      <section
        className={cn(
          "rounded-md border p-4",
          tone === "error"
            ? "border-destructive/40 bg-destructive/5"
            : "border-warning-border bg-warning/10"
        )}
        aria-live="polite"
      >
        <div className="flex items-start gap-3">
          <AlertTriangleIcon
            className={cn(
              "size-5 shrink-0",
              tone === "error" ? "text-destructive" : "text-warning-fg"
            )}
            aria-hidden
          />
          <div className="min-w-0 flex-1 space-y-1">
            <p
              className={cn(
                "text-sm font-semibold",
                tone === "error" ? "text-destructive" : "text-warning-fg"
              )}
            >
              {headline}
            </p>
            <p
              className={cn(
                "text-xs leading-snug",
                tone === "error" ? "text-destructive/90" : "text-warning-fg"
              )}
            >
              {description}
              {elapsedLabel ? (
                <>
                  {" "}
                  <span className="text-muted-foreground">· {elapsedLabel}</span>
                </>
              ) : null}
            </p>
          </div>
          {!isInFlight && (
            <Button
              type="button"
              size="sm"
              variant={tone === "error" ? "destructive" : "default"}
              onClick={() => setConfirmOpen(true)}
              disabled={redeploying}
              className="shrink-0 gap-1"
            >
              {redeploying ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <RotateCwIcon className="size-4" />
              )}
              {t("cta")}
            </Button>
          )}
        </div>
      </section>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("confirm.title", { slug: appSlug })}
        description={t("confirm.description")}
        confirmLabel={t("confirm.confirm")}
        destructive={tone === "error"}
        onConfirm={handleConfirm}
      />
    </>
  );
}
