import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import spanish from "@/messages/es.json";

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

/** An add in flight; its submit action stays disabled. */
export const Loading: Story = { args: { adding: true } };

/** One environment with no overrides (no environment picker). */
export const Empty: Story = { args: { envs: [ENVIRONMENTS[1]] } };

/** A confirmed empty environment list hides the section. */
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

/** Clearing one override leaves the other rows available. */
export const Clearing: Story = {
  args: {
    clearing: new Set([JSON.stringify([ENVIRONMENTS[0].id, ENVIRONMENTS[0].settings[0].key])]),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole("button", { name: `Clear ${ENVIRONMENTS[0].settings[0].key} override` })
    ).toBeDisabled();
    await expect(
      canvas.getByRole("button", { name: `Clear ${ENVIRONMENTS[0].settings[1].key} override` })
    ).toBeEnabled();
  },
};

export const Reading: Story = { args: { envs: [], loading: true } };
export const ReadError: Story = {
  args: { envs: [], error: "RAW_ENV_READ_DIAGNOSTIC", onRetry: () => {} },
};
export const CachedReadError: Story = {
  args: { error: "RAW_ENV_READ_DIAGNOSTIC", onRetry: () => {} },
};
export const SpanishWidth768: Story = {
  render: (args) => (
    <NextIntlClientProvider locale="es" messages={spanish}>
      <div style={{ width: 768 }}>
        <EnvironmentSettingsView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
