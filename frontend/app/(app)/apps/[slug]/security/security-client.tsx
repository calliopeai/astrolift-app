"use client";

import { AppSecurityScreen } from "@/components/screens/apps/security/AppSecurityScreen";
import { useAppSecurity } from "@/components/screens/apps/security/use-app-security";

import { AppTabs } from "../components/app-tabs";

import { AccessCard } from "./access-card";

export function AppSecurityClient({ slug }: { slug: string }) {
  const security = useAppSecurity(slug);
  const a = security.app;

  return (
    <AppSecurityScreen
      {...security}
      tabs={a ? <AppTabs slug={a.slug} active="security" /> : null}
      access={a ? <AccessCard appSlug={a.slug} /> : null}
    />
  );
}
