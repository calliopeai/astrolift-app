"use client";

import { AppDoctorPanelView } from "@/components/screens/apps/homes/AppDoctorPanel";
import { useAppDoctor } from "@/components/screens/apps/homes/use-app-doctor";

/** App doctor (#1550): probes on demand, never on mount. */
export function AppDoctorPanel({ appSlug }: { appSlug: string }) {
  return <AppDoctorPanelView {...useAppDoctor(appSlug)} />;
}
