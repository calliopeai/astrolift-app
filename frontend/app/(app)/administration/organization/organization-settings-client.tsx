"use client";

import { OrganizationSettings } from "@/components/screens/administration/organization/OrganizationSettings";
import { useOrganizationSettings } from "@/components/screens/administration/organization/use-organization-settings";

import { HouseThemeCard } from "./house-theme-card";
import { ModulesCard } from "./modules-card";
import { TrustedDomainsCard } from "./trusted-domains-card";

export function OrganizationSettingsClient() {
  const settings = useOrganizationSettings();
  return (
    <OrganizationSettings
      {...settings}
      houseTheme={settings.org ? <HouseThemeCard org={settings.org} /> : null}
      trustedDomains={<TrustedDomainsCard />}
      modules={<ModulesCard />}
    />
  );
}
