"use client";

import { AlertEventDetail } from "@/components/screens/alerts/AlertEventDetail";
import { useAlertEventDetail } from "@/components/screens/alerts/use-alert-event-detail";

/** Alert event detail (#1106), wired to one event id. */
export function AlertEventDetailClient({ id }: { id: string }) {
  return <AlertEventDetail {...useAlertEventDetail(id)} />;
}
