"use client";

/**
 * Activity (spec 44 §4.3, Builder): what just happened in the organization
 * (deploys, config syncs, scaling, cluster bring-in, secret rotations),
 * newest first, as the ActivityFeed Feed: it scrolls in its own frame and
 * loads older events on the cursor as the reader nears the end (rule 5).
 * Pure view; the data is useRecentActivity.
 */

import { ActivityIcon } from "lucide-react";
import Link from "next/link";

import { ActivityFeed, type ActivityFeedProps } from "@/components/ActivityFeed";
import { Panel } from "@/components/panel/Panel";
import { useRecentActivity } from "@/components/use-recent-activity";

import type { HomePanelProps } from "../registry";

export interface ActivityPanelViewProps extends ActivityFeedProps {
  panel: HomePanelProps["panel"];
}

export function ActivityPanelView({ panel, ...feed }: ActivityPanelViewProps) {
  return (
    <Panel
      title={panel.title}
      icon={<ActivityIcon className="size-4" />}
      span={panel.span}
      actions={
        <Link href={panel.href} className="text-primary text-xs font-medium hover:underline">
          Audit log
        </Link>
      }
    >
      <ActivityFeed maxHeight="max-h-80" {...feed} />
    </Panel>
  );
}

/** Registered on Home as `activity`. */
export function ActivityPanel({ panel }: HomePanelProps) {
  return <ActivityPanelView panel={panel} {...useRecentActivity()} />;
}
