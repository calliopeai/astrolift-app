"use client";

import type * as React from "react";

import { PageShell } from "@/components/PageShell";
import { SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";

export interface NotificationsScreenProps {
  /** Which section is shown (`?section=`); only that one is mounted and fetches. */
  section: SectionSelection;
  /** The inbox feed (NotificationsInbox), filled by the route. */
  inbox: React.ReactNode;
  /** The alert-subscriptions list (AlertSubscriptionsView), filled by the route. */
  alertSubscriptions: React.ReactNode;
}

/**
 * Settings > Notifications: the inbox and the alert subscriptions, one
 * section at a time (list rules 1 to 3), so the page holds one list and
 * fetches only the section on screen. Pure.
 */
export function NotificationsScreen({
  section,
  inbox,
  alertSubscriptions,
}: NotificationsScreenProps) {
  return (
    <PageShell
      title="Notifications"
      description="Your inbox: deploy approvals, failure alerts, invitations, quota warnings."
    >
      <SettingsPage
        single={section}
        sections={[
          { id: "inbox", title: "Inbox", content: inbox },
          { id: "alerts", title: "App alert subscriptions", content: alertSubscriptions },
        ]}
      />
    </PageShell>
  );
}
