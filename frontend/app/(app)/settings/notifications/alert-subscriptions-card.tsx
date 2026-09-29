"use client";

import { AlertSubscriptionsView } from "@/components/screens/settings/security/AlertSubscriptions";
import { useAlertSubscriptions } from "@/components/screens/settings/security/use-alert-subscriptions";

/** App alert-subscription matrix. Markup lives in AlertSubscriptionsView. */
export function AlertSubscriptionsCard() {
  return <AlertSubscriptionsView {...useAlertSubscriptions()} />;
}
