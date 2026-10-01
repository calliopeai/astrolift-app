"use client";

import { Loader2Icon, RefreshCwIcon } from "lucide-react";
import { useNow, useTranslations } from "next-intl";

import { Can } from "@/components/Can";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useResyncSource } from "./use-resync-source";

export type ResyncSourceViewProps = Pick<
  ReturnType<typeof useResyncSource>,
  "loading" | "onResync"
> & {
  unavailable?: boolean;
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
  unavailable = false,
}: ResyncSourceViewProps) {
  const t = useTranslations("apps.settings.resync");
  const agentT = useTranslations("apps.settings.agentResyncFlow");
  const now = useNow({ updateInterval: 60_000 });
  const fmt = useFormatters();

  return (
    <Section
      title={agentMode ? agentT("title") : t("title")}
      description={agentMode ? agentT("description") : t("description")}
      action={
        <Can permission="app.update">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                onClick={onResync}
                disabled={loading || unavailable}
              >
                {loading ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCwIcon className="size-3.5" />
                )}
                {loading ? t("syncing") : agentMode ? agentT("button") : t("button")}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">
              {agentMode ? agentT("buttonTooltip") : t("buttonTooltip")}
            </TooltipContent>
          </Tooltip>
        </Can>
      }
    >
      <p className="text-muted-foreground text-2xs">
        {lastResyncAt ? (
          <>
            {t("last")}{" "}
            <span className="text-foreground">{fmt.formatRelativeTime(lastResyncAt, now)}</span>.
          </>
        ) : (
          t("never")
        )}
      </p>
    </Section>
  );
}
