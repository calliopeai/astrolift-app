"use client";

import { ServerIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";

export type InstallManagedModel = { modelId: string; replicas: number | null };

/**
 * The model the install serves on its own GPUs (calliope-installer#446).
 * Nothing in the console created it and nothing here may change it, so it
 * sits above the deployments list as one read-only line, with no link.
 */
export function InstallManagedModelCard({ model }: { model: InstallManagedModel }) {
  const t = useTranslations("models.installModel");
  return (
    <section
      aria-label={t("title")}
      className="flex min-w-0 flex-wrap items-start gap-3 rounded-md border p-3 text-sm"
    >
      <ServerIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="font-medium">{t("title")}</span>
          <Badge variant="secondary" className="text-2xs">
            {t("badge")}
          </Badge>
        </div>
        <p className="truncate font-mono text-xs" title={model.modelId}>
          {model.modelId}
        </p>
        <p className="text-muted-foreground text-xs">
          {model.replicas === null
            ? t("replicasUnknown")
            : t("replicas", { count: model.replicas })}
          {" · "}
          {t("description")}
        </p>
      </div>
    </section>
  );
}
