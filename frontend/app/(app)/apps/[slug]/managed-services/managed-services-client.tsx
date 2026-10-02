"use client";

import { ManagedServicesScreen } from "@/components/screens/apps/managed-services/ManagedServicesScreen";
import { useManagedServices } from "@/components/screens/apps/managed-services/use-managed-services";

import { EmailDetailSheet } from "./email-detail-sheet";
import { ServiceDetailSheet } from "./service-detail-sheet";

/**
 * The Managed services section of the Workloads tab. The screen owns the markup; the email sheet and
 * the service detail sheet get containers so their queries run only while
 * the service they describe is open.
 */
export function ManagedServicesClient({ slug }: { slug: string }) {
  return (
    <ManagedServicesScreen
      {...useManagedServices(slug)}
      slug={slug}
      renderEmailDetail={(svc, onOpenChange) => (
        <EmailDetailSheet
          managedServiceId={svc.id}
          serviceName={svc.name || svc.kind}
          appSlug={slug}
          serviceConfig={svc.config ?? {}}
          open
          onOpenChange={onOpenChange}
        />
      )}
      renderServiceDetail={(svc, onOpenChange) => (
        <ServiceDetailSheet appSlug={slug} service={svc} onOpenChange={onOpenChange} />
      )}
    />
  );
}
