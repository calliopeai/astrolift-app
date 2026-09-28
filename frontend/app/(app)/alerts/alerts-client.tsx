"use client";

import { AlertsScreen } from "@/components/screens/alerts/AlertsScreen";
import { useAlerts } from "@/components/screens/alerts/use-alerts";

/** Alerts, wired: the rule and event walks, counts and mutations. */
export function AlertsClient() {
  return <AlertsScreen {...useAlerts()} />;
}
