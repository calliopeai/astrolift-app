"use client";

import { OrganizationSettings } from "@/components/screens/administration/organization/OrganizationSettings";
import { useOrganizationSettings } from "@/components/screens/administration/organization/use-organization-settings";
import { useSettingsSection } from "@/components/settings/use-settings-section";

import { HouseThemeCard } from "./house-theme-card";
import { ModulesCard } from "./modules-card";
import { TrustedDomainsCard } from "./trusted-domains-card";

/** One section at a time (`?section=`): each card's hook runs only while it is shown. */
export function OrganizationSettingsClient() {
  const settings = useOrganizationSettings();
  return (
    <OrganizationSettings
      {...settings}
      section={useSettingsSection()}
      houseTheme={settings.org ? <HouseThemeCard org={settings.org} /> : null}
      trustedDomains={<TrustedDomainsCard />}
      modules={<ModulesCard />}
    />
  );
}
