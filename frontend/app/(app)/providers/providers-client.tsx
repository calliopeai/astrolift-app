"use client";

import { ProvidersScreen } from "@/components/screens/providers/ProvidersScreen";
import { useProvidersTab } from "@/components/screens/providers/use-providers-tab";

import { IdentityProvidersPanel } from "../settings/identity-provider/identity-provider-client";
import { SourceProvidersPanel } from "../settings/source-providers/source-providers-client";
import { CloudProvidersPanel } from "./cloud-providers-panel";

/**
 * Unified Providers page (#887, #889, #890). Each tab's panel is a container
 * with its own queries; only the active one is mounted.
 */
export function ProvidersClient() {
  return (
    <ProvidersScreen
      {...useProvidersTab()}
      panels={{
        cloud: <CloudProvidersPanel />,
        source: <SourceProvidersPanel />,
        identity: <IdentityProvidersPanel />,
      }}
    />
  );
}
