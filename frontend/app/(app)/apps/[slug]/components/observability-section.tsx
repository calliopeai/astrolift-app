"use client";

import { ObservabilitySectionView } from "@/components/screens/apps/overview/ObservabilitySection";
import { useObservabilitySummary } from "@/components/screens/apps/overview/use-observability-summary";

/**
 * Compact observability summary on the app overview. The view lives in
 * components/screens/apps/overview/ObservabilitySection; this container
 * runs its hook.
 */
export function ObservabilitySection({ appSlug }: { appSlug: string }) {
  return <ObservabilitySectionView appSlug={appSlug} {...useObservabilitySummary(appSlug)} />;
}
