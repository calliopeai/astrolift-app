"use client";

import {
  ServiceDetailSheet as ServiceDetailSheetView,
  type ManagedServiceRow,
} from "@/components/screens/apps/managed-services/ServiceDetailSheet";

import { ManagedServiceMetrics } from "../components/managed-service-metrics";

/** The generic service detail sheet (#709), wired to the service's metrics query. */
export function ServiceDetailSheet({
  service,
  onOpenChange,
}: {
  service: ManagedServiceRow | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <ServiceDetailSheetView
      service={service}
      onOpenChange={onOpenChange}
      metrics={service ? <ManagedServiceMetrics managedServiceId={service.id} /> : null}
    />
  );
}
