import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

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
  ORG_HIDES_RESTRICTED,
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
function SingleSection({
  initial,
  org = orgSettings.org,
}: {
  initial: string;
  org?: typeof orgSettings.org;
}) {
  const section = useLocalSettingsSection(initial);
  return (
    <OrganizationSettings
      {...orgSettings}
      org={org}
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

/**
 * User policies with the org default for settings members can't change set to
 * hide (#2154). It applies to members who haven't chosen for themselves.
 */
export const RestrictedSettingsDefault: Story = {
  render: () => <SingleSection initial="user-policies" org={ORG_HIDES_RESTRICTED} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const group = canvas.getByRole("radiogroup", { name: "Settings members can't change" });
    const hide = within(group).getByRole("radio", { name: /Hide/ });
    const show = within(group).getByRole("radio", { name: /Show read-only/ });
    await expect(hide).toHaveAttribute("aria-checked", "true");
    await expect(canvas.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.click(show);
    await expect(show).toHaveAttribute("aria-checked", "true");
    await expect(canvas.getByRole("button", { name: "Save" })).toBeEnabled();
  },
};
