"use client";

import type * as React from "react";

import { ActivityTimelineView } from "@/components/screens/apps/overview/ActivityTimeline";
import { useActivityTimeline } from "@/components/screens/apps/overview/use-activity-timeline";

/**
 * Activity panel: the hook pages the app's events on their cursor, the view
 * owns the markup. `strip` is the deploy heatmap the overview shows above
 * the feed.
 */
export function ActivityTimeline({ appSlug, strip }: { appSlug: string; strip?: React.ReactNode }) {
  return <ActivityTimelineView {...useActivityTimeline(appSlug)} strip={strip} />;
}
