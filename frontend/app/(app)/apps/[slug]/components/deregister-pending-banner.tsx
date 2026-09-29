"use client";

import { DeregisterPendingBannerView } from "@/components/screens/apps/overview/DeregisterPendingBanner";
import { useDeregisterPending } from "@/components/screens/apps/overview/use-deregister-pending";

export {
  clearDeregisterPending,
  DEREGISTER_GRACE_MS,
  deregisterPendingKey,
  recordDeregisterPending,
} from "@/components/screens/apps/overview/use-deregister-pending";

/** Deregister grace-period cancel banner (#436 B) wired to one app. */
export function DeregisterPendingBanner({ appSlug }: { appSlug: string }) {
  return <DeregisterPendingBannerView {...useDeregisterPending(appSlug)} />;
}
