import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { PLATFORM_SETTINGS, PLATFORM_SETTINGS_ROWS } from "./platform-settings-fixtures";
import { PlatformSettingsScreen } from "./PlatformSettingsScreen";

const meta: Meta = {
  title: "Screens/Administration/Insights/Platform settings",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <PlatformSettingsScreen {...PLATFORM_SETTINGS} /> };

export const Loading: Story = {
  render: () => <PlatformSettingsScreen {...PLATFORM_SETTINGS} settings={[]} loading />,
};

export const QueryError: Story = {
  render: () => (
    <PlatformSettingsScreen
      {...PLATFORM_SETTINGS}
      settings={[]}
      error={new Error("only the platform operator may do this")}
    />
  ),
};

/** One setting mid-save: its controls show progress, the others lock. */
export const Saving: Story = {
  render: () => (
    <PlatformSettingsScreen {...PLATFORM_SETTINGS} pendingKey="AGENT_EGRESS_ALLOW_CIDRS" />
  ),
};

/** A sensitive setting asks before it saves, naming what can break. */
export const ConfirmsSensitive: Story = {
  render: () => <PlatformSettingsScreen {...PLATFORM_SETTINGS} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    const input = c.getByRole("textbox", { name: "Value of AGENT_POD_UID" });
    await userEvent.clear(input);
    await userEvent.type(input, "42043");
    await userEvent.click(c.getAllByRole("button", { name: "Save" })[0]);
    const dialog = await within(document.body).findByRole("alertdialog");
    await expect(within(dialog).getByText(/breaks every non-root agent pod/)).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  render: () => (
    <PlatformSettingsScreen
      {...PLATFORM_SETTINGS}
      settings={PLATFORM_SETTINGS_ROWS.map((row) => ({
        ...row,
        key: `${row.key}_WITH_A_VERY_LONG_SUFFIX_THAT_WRAPS`,
        helpText: row.helpText.repeat(4),
      }))}
    />
  ),
};
