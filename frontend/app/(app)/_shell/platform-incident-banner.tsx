"use client";

import { PlatformIncidentBanner } from "@/components/PlatformIncidentBanner";
import { usePlatformIncidents } from "@/hooks/use-platform-incidents";

/** The shell's wiring for PlatformIncidentBanner: the status page's live incidents. */
export function PlatformIncidentBannerContainer() {
  const statusPageUrl = process.env.NEXT_PUBLIC_STATUS_PAGE_URL;
  const { incidents, dismiss } = usePlatformIncidents(statusPageUrl);
  return (
    <PlatformIncidentBanner
      statusPageUrl={statusPageUrl}
      incidents={incidents}
      onDismiss={dismiss}
    />
  );
}
