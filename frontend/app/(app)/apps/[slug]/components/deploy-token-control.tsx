"use client";

import { DeployTokenControlView } from "@/components/screens/apps/controls/DeployTokenControl";
import { useDeployToken } from "@/components/screens/apps/controls/use-deploy-token";

/** Deploy token card: the hook owns the token lifecycle, the view the markup. */
export function DeployTokenControl({ appSlug }: { appSlug: string }) {
  return <DeployTokenControlView {...useDeployToken(appSlug)} />;
}
