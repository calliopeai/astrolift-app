import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AnonymizeUserDialog } from "./AnonymizeUserDialog";

const meta: Meta = {
  title: "Screens/Members/AnonymizeUserDialog",
  parameters: { layout: "centered" },
};
export default meta;

type Story = StoryObj;

const noop = () => {};

export const Open: Story = {
  render: () => (
    <AnonymizeUserDialog name="ada" onOpenChange={noop} onConfirm={() => Promise.resolve(true)} />
  ),
};

/** The anonymize call fails: the dialog stays open for a retry. */
export const Failing: Story = {
  render: () => (
    <AnonymizeUserDialog name="ada" onOpenChange={noop} onConfirm={() => Promise.resolve(false)} />
  ),
};

export const LongName: Story = {
  render: () => (
    <AnonymizeUserDialog
      name="maximiliana.vandersloot-oyelaran.regional-compliance-and-release-coordination"
      onOpenChange={noop}
      onConfirm={() => Promise.resolve(true)}
    />
  ),
};

export const Closed: Story = {
  render: () => (
    <div className="text-muted-foreground p-6 text-sm">
      <AnonymizeUserDialog
        name={null}
        onOpenChange={noop}
        onConfirm={() => Promise.resolve(true)}
      />
      The dialog is closed until a row asks for it.
    </div>
  ),
};
