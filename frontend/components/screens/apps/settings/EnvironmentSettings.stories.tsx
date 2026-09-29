import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ENV_SETTINGS, ENVIRONMENTS, LONG } from "./app-settings-members.fixtures";
import { EnvironmentSettingsView } from "./EnvironmentSettings";

const meta: Meta<typeof EnvironmentSettingsView> = {
  title: "Screens/Apps/Settings/EnvironmentSettings",
  component: EnvironmentSettingsView,
  args: ENV_SETTINGS,
};
export default meta;

type Story = StoryObj<typeof EnvironmentSettingsView>;

export const Full: Story = {};

/** An add in flight. The section has no loading skeleton; it hides until envs land. */
export const Loading: Story = { args: { adding: true } };

/** One environment with no overrides (no environment picker). */
export const Empty: Story = { args: { envs: [ENVIRONMENTS[1]] } };

/** No error state; a failed save is a toast. Shown: no environments, so nothing renders. */
export const NoEnvironments: Story = { args: { envs: [] } };

export const LongStrings: Story = {
  args: {
    envs: [
      {
        ...ENVIRONMENTS[0],
        name: LONG,
        settings: [{ id: "s-9", key: LONG, value: LONG }],
      } as (typeof ENVIRONMENTS)[number],
    ],
  },
};
