import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ProfilePreferences } from "./ProfilePreferences";
import { LOCALE_SWITCH, TIMEZONE } from "./settings-shell-account.fixtures";

/**
 * Settings › Profile: language and timezone. Neither card has a loading or
 * error state of its own (a failed save toasts); the closest real ones are
 * the saving and switching states.
 */
const meta: Meta = { title: "Screens/Settings/Account/ProfilePreferences" };
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <ProfilePreferences localeSwitch={LOCALE_SWITCH} timezone={TIMEZONE} />,
};

/** No saved override: the browser-detected zone applies. */
export const NoOverride: Story = {
  render: () => (
    <ProfilePreferences localeSwitch={LOCALE_SWITCH} timezone={{ ...TIMEZONE, savedTz: null }} />
  ),
};

/** The browser cannot list zones (no Intl.supportedValuesOf): only the no-override option. */
export const EmptyZoneList: Story = {
  render: () => (
    <ProfilePreferences
      localeSwitch={LOCALE_SWITCH}
      timezone={{ ...TIMEZONE, timezones: [], savedTz: null }}
    />
  ),
};

export const Saving: Story = {
  render: () => (
    <ProfilePreferences
      localeSwitch={{ ...LOCALE_SWITCH, pending: true }}
      timezone={{ ...TIMEZONE, saving: true }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <ProfilePreferences
      localeSwitch={LOCALE_SWITCH}
      timezone={{
        ...TIMEZONE,
        browserTz: "America/Argentina/ComodRivadavia",
        savedTz: "America/North_Dakota/New_Salem",
        timezones: ["America/Argentina/ComodRivadavia", "America/North_Dakota/New_Salem"],
      }}
    />
  ),
};
