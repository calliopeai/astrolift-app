"use client";

/**
 * Generic managed-service detail sheet (#709).
 *
 * Aurora Serverless + S3 Bucket rows on the managed-services page used
 * to be display-only. Now every kind that isn't email opens this sheet
 * showing identity metadata + the existing ManagedServiceMetricsPanel
 * (PR #720, which already covers postgres + object_store).
 *
 * Email rows have their own kind-specific sheet (email-detail-sheet)
 * because deliverability surfaces are much richer than what the
 * metrics panel covers.
 *
 * Future kinds (redis, mysql, kafka, sqs, pubsub) drop in automatically
 * as soon as the backend metrics resolver supports them — the sheet
 * itself doesn't need to change.
 */

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ManagedServiceMetricsPanel } from "@/components/observability/ManagedServiceMetricsPanel";

interface ManagedServiceRow {
  id: string;
  name: string;
  kind: string;
  variant?: string | null;
  environmentName: string;
  status: string;
}

interface Props {
  service: ManagedServiceRow | null;
  onOpenChange: (open: boolean) => void;
}

export function ServiceDetailSheet({ service, onOpenChange }: Props) {
  const open = service !== null;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        {service && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2 font-mono">
                {service.name}
                <Badge variant="secondary" className="font-sans">
                  {service.kind}
                </Badge>
                {service.variant ? (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {service.variant}
                  </Badge>
                ) : null}
              </SheetTitle>
              <SheetDescription>
                <span className="text-muted-foreground inline-flex items-center gap-2 text-xs">
                  <span>Environment:</span>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {service.environmentName}
                  </Badge>
                  <span>·</span>
                  <span className="capitalize">{service.status}</span>
                </span>
              </SheetDescription>
            </SheetHeader>

            <div className="mt-6 space-y-6">
              <ManagedServiceMetricsPanel managedServiceId={service.id} />
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
