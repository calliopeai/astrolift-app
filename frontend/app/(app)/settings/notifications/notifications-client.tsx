"use client";

import { NotificationsScreen } from "@/components/screens/settings/security/Notifications";
import { NotificationsInbox } from "@/components/screens/settings/security/NotificationsInbox";
import { useNotifications } from "@/components/screens/settings/security/use-notifications";
import { useSettingsSection } from "@/components/settings/use-settings-section";

import { AlertSubscriptionsCard } from "./alert-subscriptions-card";

/** Each section gets its own container, so only the one on screen fetches. */
export function NotificationsClient() {
  return (
    <NotificationsScreen
      section={useSettingsSection()}
      inbox={<InboxCard />}
      alertSubscriptions={<AlertSubscriptionsCard />}
    />
  );
}

function InboxCard() {
  return <NotificationsInbox {...useNotifications()} />;
}
