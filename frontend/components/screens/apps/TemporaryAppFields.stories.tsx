import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { TemporaryAppFields } from "./TemporaryAppFields";
import type { TemporaryAppValues } from "./temporary-app-model";

import { TEMPORARY_RULES } from "./temporary-app.fixtures";

const meta: Meta<typeof TemporaryAppFields> = {
  title: "Screens/Apps/TemporaryAppFields",
  component: TemporaryAppFields,
};
export default meta;
type Story = StoryObj<typeof TemporaryAppFields>;

function Fields({
  initial = {},
  rules = TEMPORARY_RULES,
  ...props
}: Partial<React.ComponentProps<typeof TemporaryAppFields>> & { initial?: TemporaryAppValues }) {
  const [value, onChange] = React.useState(initial);
  return <TemporaryAppFields {...props} rules={rules} value={value} onChange={onChange} />;
}
export const Permanent: Story = { render: () => <Fields /> };
export const Temporary: Story = { render: () => <Fields initial={{ expiresIn: "7d" }} /> };
export const DeleteData: Story = {
  render: () => <Fields initial={{ expiresIn: "4h", deleteDataOnExpiry: true }} />,
};
export const Required: Story = {
  render: () => <Fields rules={{ ...TEMPORARY_RULES, permanentAllowed: false }} />,
};
export const Invalid: Story = { render: () => <Fields initial={{ expiresIn: "999d" }} /> };
export const Inherited: Story = { render: () => <Fields inherited="6w" /> };
export const Loading: Story = { render: () => <Fields loading /> };
export const ErrorState: Story = { render: () => <Fields failed /> };
export const Unsupported: Story = { render: () => <Fields rules={null} /> };
export const Width768: Story = {
  render: () => (
    <div className="max-w-3xl">
      <Fields initial={{ expiresIn: "3mo" }} />
    </div>
  ),
};
