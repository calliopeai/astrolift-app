import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { DOCS_E_HELP, DOCS_E_HELP_LONG } from "./docs-e.fixtures";
import { HelpScreen } from "./HelpScreen";

const meta: Meta<typeof HelpScreen> = {
  title: "Screens/Documentation/HelpScreen",
  component: HelpScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof HelpScreen>;

/** No loading or error state: the org resolves in ActiveOrgProvider, and a missing org renders a dash. */
export const Full: Story = {
  args: { ...DOCS_E_HELP, copyDiagnostics: fn() },
  play: async ({ args, canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Copy diagnostic info" })
    );
    await expect(args.copyDiagnostics).toHaveBeenCalled();
  },
};

export const NoOrganization: Story = { args: { ...DOCS_E_HELP, org: null } };

export const Copied: Story = { args: { ...DOCS_E_HELP, copied: true } };

export const LongStrings: Story = { args: DOCS_E_HELP_LONG };
