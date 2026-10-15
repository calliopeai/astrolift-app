import * as React from "react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { CHANNELS, LONG_CHANNEL, RECENT, TEAMS_CHANNEL } from "./delivery.fixtures";
import { DeliveryChannelPicker, type DeliveryChannelPickerProps } from "./DeliveryChannelPicker";

function Harness(props: DeliveryChannelPickerProps) {
  const [value, setValue] = React.useState(props.value);
  return (
    <DeliveryChannelPicker
      {...props}
      value={value}
      onChange={(next) => {
        setValue(next);
        props.onChange(next);
      }}
    />
  );
}

const meta: Meta<typeof DeliveryChannelPicker> = {
  title: "Screens/Workflows/Delivery/DeliveryChannelPicker",
  component: DeliveryChannelPicker,
  parameters: { layout: "padded" },
  args: { channels: CHANNELS, value: [TEAMS_CHANNEL.guid], onChange: fn(), recent: RECENT },
  render: (args) => <Harness {...args} />,
};
export default meta;

type Story = StoryObj<typeof DeliveryChannelPicker>;

/** One channel chosen, and the last deliveries: a post, a refused Slack post, the owner's email. */
export const Chosen: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("checkbox", { name: /Ops Teams channel/ })).toBeChecked();
    await expect(canvas.getByText("Slack refused the post: not_in_channel")).toBeInTheDocument();
    await expect(canvas.getByText("Owner email")).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("checkbox", { name: /#data-digests/ }));
    await expect(args.onChange).toHaveBeenCalledWith([TEAMS_CHANNEL.guid, CHANNELS[1].guid]);
  },
};

/** No channels in the org yet: the picker points to where an admin adds them. */
export const NoChannels: Story = {
  args: { channels: [], value: [], recent: [] },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("link", { name: /Notifications/ })
    ).toHaveAttribute("href", "/settings/notifications?section=workflow-delivery");
  },
};

export const NothingDeliveredYet: Story = { args: { recent: [] } };

export const LongStrings: Story = {
  args: { channels: [LONG_CHANNEL], value: [LONG_CHANNEL.guid] },
  render: (args) => (
    <div style={{ width: 375 }}>
      <Harness {...args} />
    </div>
  ),
};
