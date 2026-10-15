import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { BROKEN_CHANNEL, CHANNELS, LONG_CHANNEL } from "./delivery.fixtures";
import { DeliveryChannelsPanel } from "./DeliveryChannelsPanel";

const meta: Meta<typeof DeliveryChannelsPanel> = {
  title: "Screens/Workflows/Delivery/DeliveryChannelsPanel",
  component: DeliveryChannelsPanel,
  parameters: { layout: "padded" },
  args: {
    channels: CHANNELS,
    onCreate: fn(async () => null),
    onToggle: fn(async () => {}),
    onRemove: fn(async () => {}),
  },
};
export default meta;

type Story = StoryObj<typeof DeliveryChannelsPanel>;

/** Teams, a Slack app channel and a paused email list. No credential is ever shown. */
export const Channels: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Ops Teams channel")).toBeInTheDocument();
    await expect(canvas.getByText(/C0123456789/)).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  },
};

/** Adding a Teams webhook sends the URL once, as the credential. */
export const AddTeamsWebhook: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(canvas.getByLabelText("Type"), "teams_webhook");
    await userEvent.type(canvas.getByLabelText("Name"), "Leadership");
    await userEvent.type(
      canvas.getByLabelText("Webhook URL"),
      "https://prod-01.westus.logic.azure.com/workflows/abc"
    );
    await userEvent.click(canvas.getByRole("button", { name: "Add channel" }));
    await expect(args.onCreate).toHaveBeenCalledWith({
      name: "Leadership",
      kind: "teams_webhook",
      credential: "https://prod-01.westus.logic.azure.com/workflows/abc",
    });
  },
};

/** A Slack app post needs the bot token and the channel ID. */
export const AddSlackApp: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(canvas.getByLabelText("Type"), "slack_app");
    await expect(canvas.getByLabelText("Bot token")).toBeInTheDocument();
    await expect(canvas.getByLabelText("Channel ID")).toBeInTheDocument();
  },
};

export const AddEmail: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(canvas.getByLabelText("Type"), "email");
    await userEvent.type(canvas.getByLabelText("Name"), "Ops");
    await userEvent.type(canvas.getByLabelText("Recipients"), "a@example.com, b@example.com");
    await userEvent.click(canvas.getByRole("button", { name: "Add channel" }));
    await expect(args.onCreate).toHaveBeenCalledWith({
      name: "Ops",
      kind: "email",
      emailRecipients: ["a@example.com", "b@example.com"],
    });
  },
};

/** The server refused the URL: the reason shows beside the button and the form keeps its values. */
export const AddRefused: Story = {
  args: {
    onCreate: fn(async () => "The webhook URL must be an https URL from Slack or Microsoft Teams."),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Name"), "Sneaky");
    await userEvent.type(canvas.getByLabelText("Webhook URL"), "https://example.com/hook");
    await userEvent.click(canvas.getByRole("button", { name: "Add channel" }));
    await expect(await canvas.findByRole("alert")).toHaveTextContent("https URL from Slack");
    await expect(canvas.getByLabelText("Name")).toHaveValue("Sneaky");
  },
};

export const MissingCredential: Story = { args: { channels: [BROKEN_CHANNEL] } };

export const Empty: Story = { args: { channels: [] } };

export const Loading: Story = { args: { channels: [], loading: true } };

export const LoadError: Story = {
  args: { channels: [], error: "Could not load delivery channels." },
};

export const LongStrings: Story = {
  args: { channels: [LONG_CHANNEL] },
  render: (args) => (
    <div style={{ width: 375 }}>
      <DeliveryChannelsPanel {...args} />
    </div>
  ),
};
