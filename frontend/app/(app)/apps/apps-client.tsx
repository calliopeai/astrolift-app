"use client";

import { AppsListScreen } from "@/components/screens/apps/list/AppsListScreen";
import { useAppsList } from "@/components/screens/apps/list/use-apps-list";

/** The apps registry list. The screen owns the markup; the hook owns the data. */
export function AppsClient() {
  return <AppsListScreen {...useAppsList()} />;
}
