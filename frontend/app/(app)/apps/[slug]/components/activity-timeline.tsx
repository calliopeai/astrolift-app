"use client";

import type * as React from "react";

import { ActivityTimelineView } from "@/components/screens/apps/overview/ActivityTimeline";
import { useActivityTimeline } from "@/components/screens/apps/overview/use-activity-timeline";

/**
 * Activity panel: the hook loads the app's events, the view owns the markup.
 * `strip` is the deploy heatmap the overview shows above the feed.
 */
export function ActivityTimeline({
  appSlug,
  limit = 20,
  strip,
}: {
  appSlug: string;
  limit?: number;
  strip?: React.ReactNode;
}) {
  return <ActivityTimelineView {...useActivityTimeline(appSlug)} limit={limit} strip={strip} />;
}
