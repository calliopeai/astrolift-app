import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { CONNECTION } from "./brains.fixtures";
import { ConnectBrainDialog } from "./ConnectBrainDialog";

const meta: Meta<typeof ConnectBrainDialog> = {
  title: "Screens/Brains/ConnectBrainDialog",
  component: ConnectBrainDialog,
  args: { connection: CONNECTION, onDismiss: fn() },
};
export default meta;

type Story = StoryObj<typeof ConnectBrainDialog>;

export const OneEndpoint: Story = {
  play: async ({ args }) => {
    const body = within(document.body);
    await expect(await body.findByText("https://brain.acme.example/mcp")).toBeInTheDocument();
    await expect(body.getByText(/"X-Astrolift-Brain-Token"/)).toBeInTheDocument();
    await userEvent.click(body.getByRole("button", { name: "Done" }));
    await expect(args.onDismiss).toHaveBeenCalled();
  },
};

export const TwoEndpoints: Story = {
  args: {
    connection: {
      ...CONNECTION,
      endpoints: [...CONNECTION.endpoints, "https://brain.internal.acme.example/"],
    },
  },
  play: async () => {
    const body = within(document.body);
    await expect(
      await body.findByText("https://brain.internal.acme.example/mcp")
    ).toBeInTheDocument();
    await expect(body.getByText(/"leo-brain-2"/)).toBeInTheDocument();
  },
};

export const Closed: Story = {
  args: { connection: null },
};
