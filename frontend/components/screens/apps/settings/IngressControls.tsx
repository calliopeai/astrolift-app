"use client";

import { Loader2Icon, PauseIcon, PlayIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Can } from "@/components/Can";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { useIngressControls } from "./use-ingress-controls";

export type IngressControlsViewProps = ReturnType<typeof useIngressControls>;

/**
 * Per-environment ingress pause/resume, independent of the deploy-pause
 * toggle in ControlsSection. Pausing ingress takes the env offline at the
 * edge without stopping the workloads (handy for maintenance windows where
 * you still want pods running for in-flight DB writes).
 */
export function IngressControlsView({
  envs,
  loading,
  busyIds,
  onToggle,
}: IngressControlsViewProps) {
  const t = useTranslations("apps.settings.ingress");

  return (
    <Section title={t("title")} description={t("description")}>
      {loading && envs.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{t("emptyEnvs")}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {envs.map((env) => (
            <IngressRow
              key={env.id}
              env={env}
              busy={busyIds.includes(env.id)}
              onToggle={() => void onToggle(env)}
            />
          ))}
        </div>
      )}
    </Section>
  );
}

function IngressRow({
  env,
  busy,
  onToggle,
}: {
  env: AstroliftAppEnvironment;
  busy: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations("apps.settings.ingress");
  const paused = env.ingressPaused;

  return (
    <div className="bg-card flex items-center justify-between gap-3 rounded-md border p-4">
      <div className="flex items-center gap-2">
        <StatusDot status={paused ? "warn" : "ok"} />
        <span className="text-sm font-medium capitalize">{env.name}</span>
        {paused ? (
          <Badge variant="outline" className="border-warning-border bg-warning/10 text-warning-fg">
            <PauseIcon className="size-3" />
            {t("paused")}
          </Badge>
        ) : (
          <Badge variant="outline" className="border-success-border bg-success/10 text-success-fg">
            <PlayIcon className="size-3" />
            {t("live")}
          </Badge>
        )}
      </div>
      <Can permission="app.deploy">
        <Button
          size="sm"
          variant={paused ? "default" : "outline"}
          onClick={onToggle}
          disabled={busy}
        >
          {busy ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : paused ? (
            <PlayIcon className="size-3.5" />
          ) : (
            <PauseIcon className="size-3.5" />
          )}
          {paused ? t("resume") : t("pause")}
        </Button>
      </Can>
    </div>
  );
}
