import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { AdministrationShell } from "./AdministrationShell";
import { LONG_ARN, LONG_SHA, LONG_URL } from "./fixtures";

const meta: Meta<typeof AdministrationShell> = {
  title: "Screens/Administration/Organization/AdministrationShell",
  component: AdministrationShell,
  parameters: { layout: "padded" },
  args: {
    fnKey: "organization",
    title: "Organization",
    description: "Edit the platform's organization-level identity and retention defaults.",
    children: <p className="text-sm">Section content renders here.</p>,
  },
};
export default meta;

type Story = StoryObj<typeof AdministrationShell>;

/** `Admin ▾ › Organization`: the switcher lists every Admin function, Organization checked. */
export const Full: Story = {};

export const WithPrimaryAction: Story = {
  args: {
    fnKey: "metrics",
    title: "Platform metrics",
    primaryAction: <Button size="sm">Refresh</Button>,
  },
};

export const Loading: Story = {
  args: {
    children: (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-10 w-full max-w-md" />
        <Skeleton className="h-10 w-full max-w-md" />
      </div>
    ),
  },
};

export const Empty: Story = { args: { description: undefined, children: null } };

/** A function the rail does not know: nothing in the switcher is checked. */
export const Error: Story = {
  args: { fnKey: "unknown", title: "Unknown", children: "This section failed to load." },
};

export const LongStrings: Story = {
  args: {
    title: `Organization ${LONG_SHA}`,
    description: `Identity for ${LONG_URL}, assuming ${LONG_ARN}.`,
  },
};

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <AdministrationShell {...args} title={`Organization ${LONG_SHA}`} description={LONG_URL} />
    </div>
  ),
};
