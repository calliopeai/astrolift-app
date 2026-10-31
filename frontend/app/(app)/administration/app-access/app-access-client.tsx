"use client";

import { AppAccessOverviewScreen } from "@/components/screens/administration/app-access/AppAccessOverviewScreen";
import { useAppAccessOverview } from "@/components/screens/administration/app-access/use-app-access-overview";

export function AppAccessClient() {
  return <AppAccessOverviewScreen {...useAppAccessOverview()} />;
}
