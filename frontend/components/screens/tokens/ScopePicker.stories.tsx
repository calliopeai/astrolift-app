import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { SCOPE_CATALOG_LONG, SCOPE_PICKER } from "@/components/screens/teams/teams-tokens.fixtures";

import { ScopePicker, type ScopePickerProps } from "./ScopePicker";

const meta: Meta = { title: "Screens/Tokens/ScopePicker" };
export default meta;

type Story = StoryObj;

function Picker({
  initial = ["read:apps", "read:clusters", "mcp:read"],
  ...props
}: Omit<ScopePickerProps, "value" | "onChange"> & { initial?: string[] }) {
  const [value, setValue] = React.useState<string[]>(initial);
  return <ScopePicker {...props} value={value} onChange={setValue} />;
}

/** The default selection; one scope unavailable to the caller's roles. */
export const Full: Story = { render: () => <Picker {...SCOPE_PICKER} /> };

/** Admin selected: the plain warning shows. */
export const AdminSelected: Story = {
  render: () => <Picker {...SCOPE_PICKER} initial={["admin"]} />,
};

/** Nothing picked yet. */
export const Empty: Story = { render: () => <Picker {...SCOPE_PICKER} initial={[]} /> };

export const Loading: Story = {
  render: () => <Picker {...SCOPE_PICKER} catalog={undefined} loading />,
};

export const LoadFailed: Story = {
  render: () => (
    <Picker
      {...SCOPE_PICKER}
      catalog={undefined}
      error={new globalThis.Error("upstream timed out")}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Picker {...SCOPE_PICKER} catalog={SCOPE_CATALOG_LONG} />,
};
