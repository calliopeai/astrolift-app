import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  houseTheme,
  modules,
  modulesLoading,
  orgSettings,
  orgSettingsLoading,
  orgSettingsLong,
  orgSettingsNoOrg,
  orgSettingsSaveFails,
  trustedDomains,
  trustedDomainsEmpty,
  trustedDomainsLoading,
  trustedDomainsLong,
  modulesLong,
  ORG_LONG,
} from "./fixtures";
import { HouseThemeCard } from "./HouseThemeCard";
import { ModulesCard } from "./ModulesCard";
import { OrganizationSettings } from "./OrganizationSettings";
import { TrustedDomainsCard } from "./TrustedDomainsCard";

const meta: Meta<typeof OrganizationSettings> = {
  title: "Screens/Administration/Organization/OrganizationSettings",
  component: OrganizationSettings,
  parameters: { layout: "fullscreen" },
  args: {
    ...orgSettings,
    houseTheme: <HouseThemeCard {...houseTheme} />,
    trustedDomains: <TrustedDomainsCard {...trustedDomains} />,
    modules: <ModulesCard {...modules} />,
  },
};
export default meta;

type Story = StoryObj<typeof OrganizationSettings>;

export const Full: Story = {};

export const Loading: Story = {
  args: {
    ...orgSettingsLoading,
    trustedDomains: <TrustedDomainsCard {...trustedDomainsLoading} />,
    modules: <ModulesCard {...modulesLoading} />,
  },
};

/** No active org resolved: the form is empty, Save is disabled, house theme is hidden. */
export const Empty: Story = {
  args: {
    ...orgSettingsNoOrg,
    trustedDomains: <TrustedDomainsCard {...trustedDomainsEmpty} />,
  },
};

/** Saving fails: the hook toasts the server's message and resolves false. */
export const Error: Story = { args: { ...orgSettingsSaveFails } };

export const LongStrings: Story = {
  args: {
    ...orgSettingsLong,
    houseTheme: <HouseThemeCard {...houseTheme} org={ORG_LONG} />,
    trustedDomains: <TrustedDomainsCard {...trustedDomainsLong} />,
    modules: <ModulesCard {...modulesLong} />,
  },
};
