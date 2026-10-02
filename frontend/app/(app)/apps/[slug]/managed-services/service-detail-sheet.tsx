"use client";

import {
  ServiceDetailSheet as ServiceDetailSheetView,
  type ManagedServiceRow,
} from "@/components/screens/apps/managed-services/ServiceDetailSheet";

import { useManagedResourceContext } from "@/components/screens/apps/managed-services/use-managed-resource-context";

import { ManagedServiceMetrics } from "../components/managed-service-metrics";

/** The generic service detail sheet (#709), wired to the service's metrics query. */
export function ServiceDetailSheet({
  service,
  onOpenChange,
  appSlug,
}: {
  service: ManagedServiceRow | null;
  appSlug: string;
  onOpenChange: (open: boolean) => void;
}) {
  const detail = useManagedResourceContext(service?.id ?? null, appSlug);
  return (
    <ServiceDetailSheetView
      service={service}
      onOpenChange={onOpenChange}
      current={detail.current}
      loading={detail.loading}
      refused={detail.refused}
      onRetry={detail.retry}
      metrics={
        detail.current ? (
          <ManagedServiceMetrics
            key={detail.scopeKey}
            managedServiceId={detail.current.id}
            expectedContextRevision={detail.current.contextRevision}
          />
        ) : null
      }
    />
  );
}
