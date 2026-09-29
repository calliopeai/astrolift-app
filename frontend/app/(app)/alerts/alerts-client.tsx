"use client";

import { AlertsScreen } from "@/components/screens/alerts/AlertsScreen";
import { useAlerts } from "@/components/screens/alerts/use-alerts";

/** Alerts, wired: the rules list, the counts and the rule mutations. */
export function AlertsClient() {
  return <AlertsScreen {...useAlerts()} />;
}
