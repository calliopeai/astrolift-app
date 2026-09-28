"use client";

import { ActivityTimelineView } from "@/components/screens/apps/overview/ActivityTimeline";
import { useActivityTimeline } from "@/components/screens/apps/overview/use-activity-timeline";

/** Activity card: the hook loads the app's events, the view owns the markup. */
export function ActivityTimeline({ appSlug, limit = 20 }: { appSlug: string; limit?: number }) {
  return <ActivityTimelineView {...useActivityTimeline(appSlug)} limit={limit} />;
}
