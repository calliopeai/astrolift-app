import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { BRAIN, NOT_SERVED } from "./brain-detail.fixtures";
import { BrainConnectTab } from "./BrainConnectTab";

const meta: Meta<typeof BrainConnectTab> = {
  title: "Screens/Brains/Detail/BrainConnectTab",
  component: BrainConnectTab,
  parameters: { layout: "padded" },
  args: {
    slug: BRAIN.slug,
    endpoints: BRAIN.environments,
    connecting: false,
    onConnect: fn(),
  },
};
export default meta;

type Story = StoryObj<typeof BrainConnectTab>;

/** Every endpoint's MCP URL; a token is issued only from the button. */
export const Served: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(args.onConnect).not.toHaveBeenCalled();
    await expect(
      canvas.getByText("https://platform-brain.staging.example.test/mcp")
    ).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Connect" }));
    await expect(args.onConnect).toHaveBeenCalled();
    await expect(canvas.getByText(/astro brain connect platform-brain/)).toBeInTheDocument();
  },
};

export const Connecting: Story = { args: { connecting: true } };

/** Not served: there is nothing to connect to yet. */
export const NotServed: Story = {
  args: { slug: NOT_SERVED.slug, endpoints: [] },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole("button", { name: "Connect" })
    ).not.toBeInTheDocument();
  },
};

/** Portal delivery remains an explicit action, bound to its selected endpoint. */
export const BrowserPortal: Story = {
  args: { onOpenPortal: fn() },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(args.onOpenPortal).not.toHaveBeenCalled();
    const buttons = canvas.getAllByRole("button", { name: "Open portal" });
    await userEvent.click(buttons[0]);
    await expect(args.onOpenPortal).toHaveBeenCalledWith(args.endpoints[0].url);
    await expect(args.onConnect).not.toHaveBeenCalled();
  },
};
