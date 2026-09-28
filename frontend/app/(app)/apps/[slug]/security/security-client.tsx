"use client";

import { AppSecurityScreen } from "@/components/screens/apps/security/AppSecurityScreen";
import { useAppSecurity } from "@/components/screens/apps/security/use-app-security";

import { AppTabs } from "../components/app-tabs";

import { AccessCard } from "./access-card";

/**
 * Security scans for an app or agent. The Access tab passes `edge={false}`:
 * it shows the edge access rule as a section of its own.
 */
export function AppSecurityClient({ slug, edge = true }: { slug: string; edge?: boolean }) {
  const security = useAppSecurity(slug);
  const a = security.app;

  return (
    <AppSecurityScreen
      {...security}
      tabs={a ? <AppTabs slug={a.slug} active="security" /> : null}
      access={a && edge ? <AccessCard appSlug={a.slug} /> : null}
    />
  );
}
