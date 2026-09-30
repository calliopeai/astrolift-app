import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import { LONG } from "./fixtures";
import { PermissionPicker, type PermissionPickerProps } from "./PermissionPicker";

/** One permission from the catalog, grouped by area, filterable, in its own scroll frame. */
const meta: Meta = { title: "Access/PermissionPicker", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

function Picker({
  initial = null,
  ...rest
}: Partial<PermissionPickerProps> & { initial?: string | null }) {
  const [value, setValue] = React.useState<string | null>(initial);
  return (
    <div className="max-w-md">
      <PermissionPicker value={value} onChange={setValue} {...rest} />
    </div>
  );
}

/** The whole generated catalog. */
export const Full: Story = { render: () => <Picker /> };

export const Picked: Story = { render: () => <Picker initial="app.deploy" /> };

/** The filter narrows to matching slugs and keeps their areas. */
export const Filtered: Story = {
  render: () => <Picker />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.type(c.getByRole("searchbox"), "deploy");
    await expect(c.getByRole("button", { name: "app.deploy" })).toBeInTheDocument();
  },
};

export const Empty: Story = { render: () => <Picker catalog={[]} /> };

export const LongStrings: Story = {
  render: () => (
    <Picker catalog={[`app.${LONG}`, `${LONG}.read`, "app.read"]} initial={`app.${LONG}`} />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Picker initial="agent.dispatch" />
    </div>
  ),
};

export const Japanese: Story = {
  render: () => <Picker initial="app.deploy" />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
