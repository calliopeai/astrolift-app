"use client";

import { FlameIcon, Loader2Icon } from "lucide-react";
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useForceRedeploy } from "./use-force-redeploy";

export type ForceRedeployViewProps = ReturnType<typeof useForceRedeploy>;

/**
 * Destructive recovery card for wedged apps (#389 + #436 D).
 *
 * Three steps run in order: cancel in-flight Deployment rows, delete
 * the per-workload k8s objects (Deployment / Service / Ingress /
 * CronJob plus bare-slug fallbacks), then re-dispatch the deploy CI
 * workflow. Permission gate is `app.deploy` plus `app.update` —
 * matching the backend's stacked-permission resolver.
 *
 * Confirmation modal requires the operator to type the app's slug,
 * matching the backend's `confirmSlug` muscle-memory guard. The
 * `Continue` button only enables once the typed slug equals the
 * app's slug exactly.
 *
 * #436 D — in-flight deployment preview: the modal fetches
 * `previewAstroliftForceRedeploy` on open and renders the list of
 * Deployment rows the recovery path will transition to FAILED.
 * Operator sees timestamps, who triggered, and image tags — enough
 * provenance to weigh "let this finish" vs "blow it away".
 */
export function ForceRedeployView({
  appSlug,
  loading,
  preview,
  previewLoading,
  loadPreview,
  onForceRedeploy,
}: ForceRedeployViewProps) {
  const t = useTranslations("apps.settings.forceRedeploy");
  const tPreview = useTranslations("apps.settings.forceRedeploy.preview");
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [typed, setTyped] = React.useState("");

  // Lazy-load the in-flight preview when the modal opens.
  React.useEffect(() => {
    if (open) {
      loadPreview();
    } else {
      setTyped("");
    }
  }, [open, loadPreview]);

  const inFlight = preview?.inFlightDeployments ?? [];
  const slugMatches = typed.trim() === appSlug;

  async function handleConfirm() {
    if (!slugMatches) return;
    if (await onForceRedeploy(typed.trim())) setOpen(false);
  }

  return (
    <Card className="border-destructive/40">
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="bg-destructive/10 text-destructive shrink-0 rounded-md p-2.5">
          <FlameIcon className="size-5" />
        </div>
        <div className="flex-1">
          <CardTitle className="text-base">{t("title")}</CardTitle>
          <CardDescription className="mt-1">{t("description")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <Can permission="app.deploy">
          <Can permission="app.update">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="destructive" onClick={() => setOpen(true)} disabled={loading}>
                  {loading ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <FlameIcon className="size-4" />
                  )}
                  {t("button")}
                </Button>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm">{t("buttonTooltip")}</TooltipContent>
            </Tooltip>
          </Can>
        </Can>
        <p className="text-muted-foreground text-2xs mt-2">{t("hint")}</p>
      </CardContent>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent className="max-h-[90vh] overflow-y-auto">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <FlameIcon className="text-destructive size-4" />
              {t("confirmTitle", { slug: appSlug })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("confirmDescription")}</AlertDialogDescription>
          </AlertDialogHeader>

          {/* In-flight deployment preview (#436 D) — the rows below
              are exactly what the recovery path transitions to FAILED
              before re-firing CI. ``inFlight.length === 0`` is the
              calm path: no live deploys to interrupt. */}
          <div className="bg-muted/40 my-2 space-y-2 rounded-md border p-3 text-xs">
            <p className="font-medium">
              {previewLoading
                ? tPreview("loading")
                : tPreview("header", { count: inFlight.length })}
            </p>
            {previewLoading ? (
              <Skeleton className="h-12 w-full" />
            ) : inFlight.length === 0 ? (
              <p className="text-muted-foreground">{tPreview("noInflight")}</p>
            ) : (
              <ul className="space-y-1.5">
                {inFlight.map((d) => (
                  <li
                    key={d.id}
                    className="border-border/60 flex flex-col gap-0.5 rounded-md border bg-transparent p-2 font-mono"
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline" className="text-2xs">
                        {d.status}
                      </Badge>
                      <span className="text-foreground">{d.environmentName}</span>
                      {d.workloadSlug ? (
                        <span className="text-muted-foreground">/ {d.workloadSlug}</span>
                      ) : null}
                      {d.imageTag ? (
                        <span className="text-muted-foreground">@ {d.imageTag}</span>
                      ) : null}
                    </div>
                    <p className="text-muted-foreground text-2xs">
                      {tPreview("triggeredBy", {
                        actor: d.triggeredByDisplay,
                        when: fmt.formatRelativeTime(d.startedAt ?? d.createdAt),
                      })}
                      {d.ciRunUrl ? (
                        <>
                          {" · "}
                          <a
                            href={d.ciRunUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="underline"
                          >
                            {tPreview("ciRun")}
                          </a>
                        </>
                      ) : null}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="grid gap-2 py-2">
            <Label htmlFor="force-redeploy-confirm" className="text-xs">
              {t.rich("typeToConfirm", {
                slug: () => <span className="text-foreground font-mono text-xs">{appSlug}</span>,
              })}
            </Label>
            <Input
              id="force-redeploy-confirm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={appSlug}
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={loading}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleConfirm();
              }}
              disabled={!slugMatches || loading}
              className="bg-destructive hover:bg-destructive/90 text-white"
            >
              {loading ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {t("button")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
