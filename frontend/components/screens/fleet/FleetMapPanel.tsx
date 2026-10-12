"use client";

import { Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Section } from "@/components/ui/section";

import { FleetMap } from "./FleetMap";
import type { useFleetMap } from "./use-fleet-map";

export type FleetMapPanelProps = ReturnType<typeof useFleetMap>;

/**
 * The live dispatch map with its loading, error and empty states (#1091 —
 * LiveFlowMap P2). Rendered on /fleet/map and in the /fleet topology card.
 */
export function FleetMapPanel({
  tasks,
  clusterLiveness,
  errorMessage,
  firstLoad,
}: FleetMapPanelProps) {
  const copy = useTranslations("fleetMap");
  const runningCount = tasks.filter((t) => t.status === "running").length;

  return (
    <Section>
      {errorMessage !== null && tasks.length === 0 && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
          {errorMessage}
        </div>
      )}

      {firstLoad && (
        <div className="flex items-center justify-center rounded-md border p-12">
          <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
        </div>
      )}

      {!firstLoad && tasks.length === 0 && errorMessage === null && (
        <div className="text-muted-foreground rounded-md border border-dashed p-12 text-center text-sm">
          <p className="text-foreground mb-1 font-medium">{copy("noActivity")}</p>
          <p>{copy("noActivityDescription")}</p>
        </div>
      )}

      {tasks.length > 0 && (
        <>
          <FleetMap tasks={tasks} clusterLiveness={clusterLiveness} />
          <p className="text-muted-foreground text-2xs">
            {copy("summary", { count: tasks.length, running: runningCount })}
          </p>
        </>
      )}
    </Section>
  );
}
