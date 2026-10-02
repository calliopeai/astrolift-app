"use client";

/**
 * Generic managed-service detail sheet (#709).
 *
 * Aurora Serverless + S3 Bucket rows on the managed-services page used
 * to be display-only. Now every kind that isn't email opens this sheet
 * showing identity metadata + the existing ManagedServiceMetricsPanel
 * (PR #720, which already covers postgres + object_store).
 *
 * Email rows have their own kind-specific sheet (EmailDetailSheet)
 * because deliverability surfaces are much richer than what the
 * metrics panel covers.
 *
 * Future kinds (redis, mysql, kafka, sqs, pubsub) drop in automatically
 * as soon as the backend metrics resolver supports them — the sheet
 * itself doesn't need to change.
 */

import * as React from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import type { ManagedResourceContextFragment } from "@/graphql/__generated__/operations";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export interface ManagedServiceRow {
  id: string;
  name: string;
  kind: string;
  variant?: string | null;
  environmentName: string;
  status: string;
}

export interface ServiceDetailSheetProps {
  service: ManagedServiceRow | null;
  onOpenChange: (open: boolean) => void;
  /** The service's metrics panel, wired to its query by the route. */
  metrics?: React.ReactNode;
  current?: ManagedResourceContextFragment | null;
  loading?: boolean;
  refused?: boolean;
  onRetry?: () => void;
}

export function ServiceDetailSheet({
  service,
  onOpenChange,
  metrics,
  current,
  loading,
  refused,
  onRetry,
}: ServiceDetailSheetProps) {
  const copy = useTranslations("projectResources.reads");
  const common = useTranslations("common");
  const displayed = current === undefined ? service : current;
  const open = service !== null;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        {service && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2 font-mono">
                {displayed?.name || copy("detail")}
                <Badge variant="secondary" className="font-sans">
                  {displayed?.kind}
                </Badge>
                {displayed?.variant ? (
                  <Badge variant="outline" className="text-2xs font-mono">
                    {displayed.variant}
                  </Badge>
                ) : null}
              </SheetTitle>
              <SheetDescription>
                <span className="text-muted-foreground flex min-w-0 flex-wrap items-center gap-2 text-xs">
                  <span>{copy("environment")}:</span>
                  <Badge variant="outline" className="text-2xs max-w-full shrink font-mono">
                    <span className="min-w-0 truncate" title={displayed?.environmentName}>
                      {displayed?.environmentName || "—"}
                    </span>
                  </Badge>
                  <span>·</span>
                  <span className="capitalize">
                    {displayed?.status &&
                      ([
                        "pending",
                        "provisioning",
                        "active",
                        "updating",
                        "deprovisioning",
                        "failed",
                      ].includes(displayed.status)
                        ? copy(`statuses.${displayed.status}`)
                        : displayed.status)}
                  </span>
                </span>
              </SheetDescription>
            </SheetHeader>

            <div className="mt-6 space-y-6">
              {loading ? (
                <p role="status">{common("loading")}</p>
              ) : refused ? (
                <div role="alert">
                  <p>{copy("refused")}</p>
                  <Button variant="outline" onClick={onRetry}>
                    {copy("reviewAgain")}
                  </Button>
                </div>
              ) : (
                <>
                  {current && (
                    <p className="text-muted-foreground font-mono text-xs break-all">
                      {current.id} · {current.clusterId} · {current.environmentId || "—"}
                    </p>
                  )}
                  {metrics}
                </>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
