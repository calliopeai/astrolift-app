"use client";

import { NotificationsScreen } from "@/components/screens/settings/security/Notifications";
import { useNotifications } from "@/components/screens/settings/security/use-notifications";

import { AlertSubscriptionsCard } from "./alert-subscriptions-card";

export function NotificationsClient() {
  return (
    <NotificationsScreen {...useNotifications()} alertSubscriptions={<AlertSubscriptionsCard />} />
  );
}
