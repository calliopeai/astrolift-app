"use client";

import { CloudProvidersPanelView } from "@/components/screens/providers/CloudProvidersPanel";
import { useCloudProviders } from "@/components/screens/providers/use-cloud-providers";

/** Cloud-provider section of the unified Providers page (#887). */
export function CloudProvidersPanel() {
  return <CloudProvidersPanelView {...useCloudProviders()} />;
}
