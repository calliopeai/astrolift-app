"use client";

import { useSearchParams } from "next/navigation";

import { alertEventsView } from "@/components/screens/alerts/alerts-list";
import { AlertEventsScreen } from "@/components/screens/alerts/AlertEventsScreen";
import { useAlertEvents } from "@/components/screens/alerts/use-alerts";

export function AlertEventsClient() {
  const view = alertEventsView(useSearchParams()?.get("view"));
  return <AlertEventsScreen {...useAlertEvents(view)} />;
}
