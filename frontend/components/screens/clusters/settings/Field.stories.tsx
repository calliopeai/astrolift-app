import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Field } from "./Field";

const meta: Meta<typeof Field> = {
  title: "Screens/Clusters/Settings/Field",
  component: Field,
  decorators: [
    (Story) => (
      <dl className="grid max-w-md grid-cols-1 gap-3">
        <Story />
      </dl>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof Field>;

export const Plain: Story = { args: { label: "Registered", value: "Sep 28, 2026, 10:14 AM" } };

export const Mono: Story = {
  args: {
    label: "API endpoint",
    mono: true,
    value: "https://A1B2C3.gr7.us-west-2.eks.amazonaws.com",
  },
};

export const Empty: Story = { args: { label: "Chart version", mono: true, value: "—" } };

export const LongValue: Story = {
  args: {
    label: "User pool ARN",
    mono: true,
    value:
      "arn:aws:cognito-idp:us-west-2:123456789012:userpool/us-west-2_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
  },
};
