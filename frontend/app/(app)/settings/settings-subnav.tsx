"use client";

import { usePathname } from "next/navigation";

import { SettingsSubnavView } from "@/components/screens/settings/shell/SettingsSubnav";

export function SettingsSubnav() {
  return <SettingsSubnavView pathname={usePathname()} />;
}
