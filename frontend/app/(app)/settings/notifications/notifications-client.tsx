"use client";

import { DeliveryChannelsPanel } from "@/components/screens/workflows/delivery/DeliveryChannelsPanel";
import { useDeliveryChannels } from "@/components/screens/workflows/delivery/use-delivery-channels";
import { InstallAlertMailClient } from "@/components/screens/settings/install-alert-mail/InstallAlertMailClient";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { NotificationsScreen } from "@/components/screens/settings/security/Notifications";
import { NotificationsInbox } from "@/components/screens/settings/security/NotificationsInbox";
import { NotificationQuietHoursPanel } from "@/components/screens/settings/security/NotificationQuietHoursPanel";
import { useNotificationQuietHours } from "@/components/screens/settings/security/use-notification-quiet-hours";
import { useNotifications } from "@/components/screens/settings/security/use-notifications";
import { useSettingsSection } from "@/components/settings/use-settings-section";

import { AlertSubscriptionsCard } from "./alert-subscriptions-card";

/** Each section gets its own container, so only the one on screen fetches. */
export function NotificationsClient() {
  const permissions = useMyPermissions();
  return (
    <NotificationsScreen
      section={useSettingsSection()}
      installAlertMail={
        !permissions.loading && !permissions.error && permissions.can("org.update") ? (
          <InstallAlertMailClient />
        ) : undefined
      }
      workflowDelivery={
        !permissions.loading && !permissions.error && permissions.can("org.update") ? (
          <DeliveryChannelsCard />
        ) : undefined
      }
      inbox={<InboxCard />}
      alertSubscriptions={<AlertSubscriptionsCard />}
      quietHours={<QuietHoursCard />}
    />
  );
}

function DeliveryChannelsCard() {
  return <DeliveryChannelsPanel {...useDeliveryChannels()} />;
}

function InboxCard() {
  return <NotificationsInbox {...useNotifications()} />;
}

function QuietHoursCard() {
  return <NotificationQuietHoursPanel {...useNotificationQuietHours()} />;
}
