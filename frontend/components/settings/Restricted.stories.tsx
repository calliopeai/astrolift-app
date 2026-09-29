import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Input } from "@/components/ui/input";

import { Restricted } from "./Restricted";

function Fields() {
  return (
    <div className="flex max-w-sm flex-col gap-2">
      <label htmlFor="restricted-endpoint" className="text-sm font-medium">
        Issuer URL
      </label>
      <Input id="restricted-endpoint" defaultValue="https://auth.example.com/realms/acme" />
    </div>
  );
}

const meta: Meta<typeof Restricted> = {
  title: "Settings/Restricted",
  component: Restricted,
  args: { permission: "cluster.update", children: <Fields /> },
};
export default meta;

type Story = StoryObj<typeof Restricted>;

export const Allowed: Story = { args: { allowed: true } };
/** Shown read-only: the same fields, disabled, and the permission named. */
export const ShowReadOnly: Story = { args: { allowed: false, mode: "show" } };
/** Hidden: nothing renders for a viewer who chose to hide what they can't change. */
export const Hidden: Story = {
  args: { allowed: false, mode: "hide" },
  render: (args) => (
    <div className="text-muted-foreground text-sm">
      Before
      <Restricted {...args} />
      After (nothing between)
    </div>
  ),
};
export const LongPermission: Story = {
  args: {
    allowed: false,
    mode: "show",
    permission: "cluster.central_auth.identity_provider.update",
  },
};
