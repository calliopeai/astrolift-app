import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { CustomAccent, Ground } from "@/lib/appearance";

import { CustomAccentPicker } from "./CustomAccentPicker";

function Demo({ initial, ground }: { initial: CustomAccent | null; ground: Ground | null }) {
  const [value, setValue] = React.useState(initial);
  return (
    <div className="max-w-md">
      <CustomAccentPicker id="story-accent" value={value} ground={ground} onChange={setValue} />
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Settings/CustomAccentPicker",
  component: Demo,
  args: { initial: null, ground: "black" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

export const Empty: Story = {};
export const InUse: Story = { args: { initial: "#3fa7d6" } };
export const TooFaint: Story = { args: { initial: "#111111" } };
export const AnyGround: Story = { args: { initial: "#3fa7d6", ground: null } };
export const FaintOnSomeGrounds: Story = { args: { initial: "#e8e2d0", ground: null } };
