"use client";

import { HomeScreen } from "@/components/home/HomeScreen";
import { useHome } from "@/components/home/use-home";

import { OnboardingHost } from "./onboarding-host";

/**
 * Home (spec 44 §4.3), at /dashboard. The screen draws the person's layout
 * from the panel registry; each panel brings its own query.
 */
export function HomeClient() {
  return <HomeScreen {...useHome()} onboarding={<OnboardingHost />} />;
}
