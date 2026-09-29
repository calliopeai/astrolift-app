"use client";

import { useHome } from "@/components/home/use-home";
import { HomeLayoutSettings } from "@/components/screens/settings/account/HomeLayoutSettings";

export function HomeSettingsClient() {
  return <HomeLayoutSettings {...useHome()} />;
}
