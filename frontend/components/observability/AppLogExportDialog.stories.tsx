import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import * as React from "react";

import {
  AppLogExportDialog,
  type AppLogExportDialogProps,
} from "@/components/observability/AppLogExportDialog";

const meta: Meta = { title: "Patterns/Observability/AppLogExportDialog" };
export default meta;

type Story = StoryObj<typeof AppLogExportDialog>;

/** Holds the open state the way the logs page does. */
function Controlled(props: Omit<AppLogExportDialogProps, "open" | "onOpenChange">) {
  const [open, setOpen] = React.useState(true);
  return <AppLogExportDialog {...props} open={open} onOpenChange={setOpen} />;
}

export const WithPod: Story = {
  args: {
    podName: "checkout-web-7d9f8b6c4-x2k9p",
    busy: false,
    onExport: fn(async () => true),
  },
  render: (args) => <Controlled {...args} />,
  play: async ({ args, canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByRole("button", { name: "Export" }));
    await waitFor(() =>
      expect(args.onExport).toHaveBeenCalledWith({
        format: "TXT",
        since: "",
        until: "",
        level: "",
        regex: "",
      })
    );
  },
};

export const NoPodSelected: Story = {
  args: { podName: null, busy: false, onExport: fn(async () => false) },
  render: (args) => <Controlled {...args} />,
};

export const Exporting: Story = {
  args: { podName: "checkout-web-7d9f8b6c4-x2k9p", busy: true, onExport: fn(async () => true) },
  render: (args) => <Controlled {...args} />,
};
