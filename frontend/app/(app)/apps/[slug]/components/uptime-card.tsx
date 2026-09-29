"use client";

import { UptimeCardView } from "@/components/screens/apps/overview/UptimeCard";
import { useUptime } from "@/components/screens/apps/overview/use-uptime";

/** Uptime card: the hook's data rendered by the view. */
export function UptimeCard({ appSlug }: { appSlug: string }) {
  return <UptimeCardView {...useUptime(appSlug)} />;
}
