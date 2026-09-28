"use client";

import { ActivityFeed } from "@/components/ActivityFeed";
import { DashboardScreen } from "@/components/screens/dashboard/DashboardScreen";
import { useDashboard } from "@/components/screens/dashboard/use-dashboard";
import { useRecentActivity } from "@/components/use-recent-activity";

import { OnboardingHost } from "./onboarding-host";

/**
 * Overview dashboard. The screen owns the markup; the activity feed gets a
 * container so its query runs only when the viewer can read the audit log.
 */
export function DashboardClient() {
  return (
    <DashboardScreen
      {...useDashboard()}
      onboarding={<OnboardingHost />}
      activity={<DashboardActivity />}
    />
  );
}

function DashboardActivity() {
  return <ActivityFeed {...useRecentActivity()} />;
}
