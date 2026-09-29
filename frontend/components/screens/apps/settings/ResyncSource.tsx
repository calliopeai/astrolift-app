"use client";

import { Loader2Icon, RefreshCwIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Can } from "@/components/Can";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useResyncSource } from "./use-resync-source";

export type ResyncSourceViewProps = ReturnType<typeof useResyncSource> & {
  lastResyncAt: string | null;
  /** Agents resync a frozen package, not a manifest. */
  agentMode?: boolean;
};

/**
 * "Resync from source" section of the settings landing (#386). The relative
 * "Last resynced" timestamp re-renders after each resync.
 */
export function ResyncSourceView({
  loading,
  onResync,
  lastResyncAt,
  agentMode = false,
}: ResyncSourceViewProps) {
  const t = useTranslations("apps.settings.resync");
  const fmt = useFormatters();

  return (
    <Section
      title={agentMode ? "Resync agent package from source" : t("title")}
      description={
        agentMode
          ? "Fetch the selected manifest and source slice, resolve skills and tools, and freeze a new immutable package without starting a run."
          : t("description")
      }
      action={
        <Can permission="app.update">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button size="sm" variant="outline" onClick={onResync} disabled={loading}>
                {loading ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCwIcon className="size-3.5" />
                )}
                {loading ? t("syncing") : agentMode ? "Resync package" : t("button")}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">{t("buttonTooltip")}</TooltipContent>
          </Tooltip>
        </Can>
      }
    >
      <p className="text-muted-foreground text-2xs">
        {lastResyncAt ? (
          <>
            {t("last")}{" "}
            <span className="text-foreground">{fmt.formatRelativeTime(lastResyncAt)}</span>.
          </>
        ) : (
          t("never")
        )}
      </p>
    </Section>
  );
}
