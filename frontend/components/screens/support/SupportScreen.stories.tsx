import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { SupportScreen } from "./SupportScreen";
import { SUPPORT, TICKET } from "./support.fixtures";

const meta: Meta<typeof SupportScreen> = {
  title: "Screens/Support/SupportScreen",
  component: SupportScreen,
  parameters: { layout: "fullscreen" },
  args: SUPPORT,
};
export default meta;

type Story = StoryObj<typeof SupportScreen>;

export const Cases: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(TICKET.title)).toBeVisible();
    await expect(canvas.getByText("Triaged")).toBeVisible();
  },
};

export const Loading: Story = { args: { tickets: [], loading: true } };

export const Empty: Story = { args: { tickets: [] } };

export const LoadFailed: Story = { args: { tickets: [], error: "PERMISSION_DENIED" } };

/** The module is switched off for this install: one clear state, no form. */
export const Unavailable: Story = { args: { unavailable: true } };

export const Drafting: Story = {
  args: { draft: { title: "Deploys stuck", body: "Since 09:00, production only.", severity: 2 } },
};

export const Created: Story = { args: { createdId: TICKET.id } };

/** No active support coverage for this deployment: Client Cove's 402. */
export const NoCoverage: Story = {
  args: {
    draft: { title: "Deploys stuck", body: "Since 09:00", severity: 1 },
    createError: "SUPPORT_COVERAGE_REQUIRED",
  },
};

export const OpenCase: Story = {
  args: { selected: TICKET, reply: "Here is the status output." },
};

export const ReplyFailed: Story = {
  args: {
    selected: TICKET,
    reply: "Here is the status output.",
    replyError: "SUPPORT_UNAVAILABLE",
  },
};
