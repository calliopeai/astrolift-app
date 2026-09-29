"use client";

import { ConfigDriftBannerView } from "@/components/screens/apps/overview/ConfigDriftBanner";
import { useConfigDriftResync } from "@/components/screens/apps/overview/use-config-drift-resync";
import type { AstroliftAppConfigDrift } from "@/graphql/registry/registry.types";

/** Config drift banner (#407 C) wired to one app. */
export function ConfigDriftBanner({
  appSlug,
  drift,
}: {
  appSlug: string;
  drift: AstroliftAppConfigDrift;
}) {
  return <ConfigDriftBannerView drift={drift} {...useConfigDriftResync(appSlug)} />;
}
