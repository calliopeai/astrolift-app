"use client";

import {
  ListObjectsDialogView,
  type ManagedServiceDialogSlotProps,
  type ManagedServiceDialogSlots,
  ManagedServicesSummaryView,
  QueueDepthDialogView,
  RevealConnectionDialogView,
  SendTestEmailDialogView,
} from "@/components/screens/apps/overview/ManagedServicesSummaryCard";
import {
  useManagedServiceObjects,
  useManagedServicesSummary,
  useQueueDepth,
  useRevealConnection,
  useSendTestEmail,
} from "@/components/screens/apps/overview/use-managed-services-summary";

import { appPath, useAppChrome } from "./app-chrome-context";

/**
 * Managed-services summary card for the app Settings landing (#401). Each
 * row's kind-specific dialog gets a container here so its hook runs only
 * for rows that carry that action.
 */
export function ManagedServicesSummaryCard({ appSlug }: { appSlug: string }) {
  const chrome = useAppChrome();
  return (
    <ManagedServicesSummaryView
      {...useManagedServicesSummary(appSlug)}
      managedServicesHref={appPath(chrome, appSlug, "managed-services")}
      dialogs={DIALOGS}
    />
  );
}

function RevealConnectionDialog(p: ManagedServiceDialogSlotProps) {
  return <RevealConnectionDialogView {...p} {...useRevealConnection(p.svc, p.open)} />;
}

function SendTestEmailDialog(p: ManagedServiceDialogSlotProps) {
  return <SendTestEmailDialogView {...p} {...useSendTestEmail(p.svc)} />;
}

function ListObjectsDialog(p: ManagedServiceDialogSlotProps) {
  return <ListObjectsDialogView {...p} {...useManagedServiceObjects(p.svc, p.open)} />;
}

function QueueDepthDialog(p: ManagedServiceDialogSlotProps) {
  return <QueueDepthDialogView {...p} {...useQueueDepth(p.svc, p.open)} />;
}

const DIALOGS: ManagedServiceDialogSlots = {
  reveal: (p) => <RevealConnectionDialog {...p} />,
  sendEmail: (p) => <SendTestEmailDialog {...p} />,
  objects: (p) => <ListObjectsDialog {...p} />,
  depth: (p) => <QueueDepthDialog {...p} />,
};
