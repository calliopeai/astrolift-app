import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

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

/** Only a confirmed server flag selects the repeated cleanup review. */
export const ReviewPrivacyCleanup: Story = {
  render: () => (
    <AnonymizeUserDialog
      targetId="USER_ANON"
      name="anon-6-7c2f19ab"
      isAnonymized={true}
      onOpenChange={noop}
      onConfirm={() => Promise.resolve(false)}
    />
  ),
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    const dialog = within(await body.findByRole("alertdialog"));
    const action = dialog.getByRole("button", { name: "Run privacy cleanup" });
    await expect(action).toBeDisabled();
    await userEvent.click(dialog.getByRole("checkbox"));
    await expect(action).toBeEnabled();
    await userEvent.click(action);
    await expect(body.getByRole("alertdialog")).toBeVisible();
    await expect(dialog.getByRole("checkbox")).toBeChecked();
  },
};

/** Unknown server state retains the ordinary first-cleanup review. */
export const UnknownPrivacyState: Story = {
  render: () => (
    <AnonymizeUserDialog
      targetId="USER_UNKNOWN"
      name="LITERAL_NAME"
      isAnonymized={null}
      onOpenChange={noop}
      onConfirm={() => Promise.resolve(false)}
    />
  ),
};
