import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";
import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

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
import { TRUSTED_DOMAINS_LIST } from "./trusted-domains-list";
import { TrustedDomainsCard } from "./TrustedDomainsCard";

/** The trusted-domains section with its list state in memory, as the hook keeps it. */
function TrustedDomains(props: typeof trustedDomains) {
  const list = useLocalListState(TRUSTED_DOMAINS_LIST);
  return <TrustedDomainsCard {...props} list={list} />;
}

const meta: Meta<typeof OrganizationSettings> = {
  title: "Screens/Administration/Organization/OrganizationSettings",
  component: OrganizationSettings,
  parameters: { layout: "fullscreen" },
  args: {
    ...orgSettings,
    houseTheme: <HouseThemeCard {...houseTheme} />,
    trustedDomains: <TrustedDomains {...trustedDomains} />,
    modules: <ModulesCard {...modules} />,
  },
};
export default meta;

type Story = StoryObj<typeof OrganizationSettings>;

export const Full: Story = {};

export const Loading: Story = {
  args: {
    ...orgSettingsLoading,
    trustedDomains: <TrustedDomains {...trustedDomainsLoading} />,
    modules: <ModulesCard {...modulesLoading} />,
  },
};

/** No active org resolved: the form is empty, Save is disabled, house theme is hidden. */
export const Empty: Story = {
  args: {
    ...orgSettingsNoOrg,
    trustedDomains: <TrustedDomains {...trustedDomainsEmpty} />,
  },
};

/** Saving fails: the hook toasts the server's message and resolves false. */
export const Error: Story = { args: { ...orgSettingsSaveFails } };

export const LongStrings: Story = {
  args: {
    ...orgSettingsLong,
    houseTheme: <HouseThemeCard {...houseTheme} org={ORG_LONG} />,
    trustedDomains: <TrustedDomains {...trustedDomainsLong} />,
    modules: <ModulesCard {...modulesLong} />,
  },
};

/** The narrowest the web console goes (spec 44 §6): the section nav becomes a select. */
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <OrganizationSettings {...args} />
    </div>
  ),
  args: LongStrings.args,
};

/** One section at a time, chosen by `?section=`, as the route mounts it. */
function SingleSection({ initial }: { initial: string }) {
  const section = useLocalSettingsSection(initial);
  return (
    <OrganizationSettings
      {...orgSettings}
      section={section}
      houseTheme={<HouseThemeCard {...houseTheme} />}
      trustedDomains={<TrustedDomains {...trustedDomains} />}
      modules={<ModulesCard {...modules} />}
    />
  );
}

export const TrustedDomainsSection: Story = {
  render: () => <SingleSection initial="trusted-domains" />,
};
