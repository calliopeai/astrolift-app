"use client";

import { IdentityProvidersScreen } from "@/components/screens/settings/identity-provider/IdentityProvidersScreen";
import { useIdentityProviders } from "@/components/screens/settings/identity-provider/use-identity-providers";

import { CreateIdentityProviderDialog } from "./create-identity-provider-dialog";

/**
 * Identity providers panel, rendered under /providers' Identity tab. The
 * screen owns the markup; the create sheet keeps its own container so its
 * mutation hook sits with it.
 */
export function IdentityProvidersPanel() {
  return (
    <IdentityProvidersScreen
      {...useIdentityProviders()}
      renderCreateSheet={(props) => <CreateIdentityProviderDialog {...props} />}
    />
  );
}
