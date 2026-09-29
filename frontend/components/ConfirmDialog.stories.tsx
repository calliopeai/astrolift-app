import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";

/** One confirmation dialog, with an optional required reason (spec 44 §7). */
const meta: Meta<typeof ConfirmDialog> = {
  title: "Primitives/ConfirmDialog",
  component: ConfirmDialog,
};
export default meta;

const SHA = "1112015d8a9b7c6e5f4d3c2b1a0f9e8d7c6b5a49";

function Demo(props: Partial<React.ComponentProps<typeof ConfirmDialog>>) {
  const [open, setOpen] = React.useState(true);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open</Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Confirm"
        onConfirm={() => new Promise((resolve) => setTimeout(resolve, 600))}
        {...props}
      />
    </>
  );
}

export const Destructive: StoryObj = {
  render: () => (
    <Demo
      title="Delete app checkout?"
      description="Soft delete: recoverable for 30 days by an org owner."
      confirmLabel="Delete app"
      destructive
    />
  ),
};

/** The #2125 case: a long unbroken SHA in the title stays inside the frame. */
export const WithReasonAndLongTitle: StoryObj = {
  render: () => (
    <Demo
      title={`Discard failed deploy ${SHA}?`}
      description="The deploy's record stays in history; its image is not pulled again."
      confirmLabel="Discard"
      destructive
      reason={{
        label: "Reason for discard",
        placeholder: "Why are you discarding this deploy?",
      }}
    />
  ),
};
