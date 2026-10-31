import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, waitFor, within } from "storybook/test";

import { Button } from "@/components/ui/button";

import {
  DefaultZoneDeleteDialog,
  type DefaultZoneDeleteDialogProps,
  type ZoneDependent,
} from "./DefaultZoneDeleteDialog";
import {
  DELETE_REFUSAL,
  DOMAIN_DEFAULT,
  LONG,
  ZONE_DEPENDENTS,
} from "./domains-environments.fixtures";

/** Removing the zone new apps resolve through (#2572): forced, and only once it is typed. */
const meta: Meta<typeof DefaultZoneDeleteDialog> = {
  title: "Screens/Domains/DefaultZoneDeleteDialog",
  component: DefaultZoneDeleteDialog,
};
export default meta;
type Story = StoryObj;

function Demo(props: Partial<DefaultZoneDeleteDialogProps>) {
  const [open, setOpen] = React.useState(true);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open</Button>
      <DefaultZoneDeleteDialog
        open={open}
        onOpenChange={setOpen}
        zone={DOMAIN_DEFAULT.zone}
        dependents={ZONE_DEPENDENTS}
        total={14}
        onConfirm={async () => true}
        {...props}
      />
    </>
  );
}

/** Lists what resolves through the zone; the removal arms on the exact zone only. */
export const WithDependents: Story = {
  render: () => <Demo />,
  play: async ({ canvasElement }) => {
    const page = within(canvasElement.ownerDocument.body);
    const dialog = within(await page.findByRole("alertdialog"));
    await expect(dialog.getByText("Environments that resolve through it: 14")).toBeVisible();
    await expect(dialog.getByText("storefront · staging")).toBeVisible();
    await expect(dialog.getByText("and 11 more")).toBeVisible();
    const remove = dialog.getByRole("button", { name: "Remove default zone" });
    await expect(remove).toBeDisabled();
    const typed = dialog.getByLabelText(`To confirm, type the zone: ${DOMAIN_DEFAULT.zone}`);
    await userEvent.type(typed, "apps.acme");
    await expect(remove).toBeDisabled();
    await userEvent.type(typed, ".example");
    await expect(remove).toBeEnabled();
  },
};

export const NoDependents: Story = {
  render: () => <Demo dependents={[]} total={0} />,
  play: async ({ canvasElement }) => {
    const dialog = within(await within(canvasElement.ownerDocument.body).findByRole("alertdialog"));
    await expect(dialog.getByText("No environment resolves through it yet.")).toBeVisible();
    await expect(dialog.queryByRole("list")).toBeNull();
  },
};

/** The server kept the zone on a removal without force, and said why. */
export const Refused: Story = {
  render: () => (
    <Demo
      dependents={DELETE_REFUSAL.items}
      total={DELETE_REFUSAL.total}
      refusal={DELETE_REFUSAL.message}
    />
  ),
  play: async ({ canvasElement }) => {
    const dialog = within(await within(canvasElement.ownerDocument.body).findByRole("alertdialog"));
    const alert = within(dialog.getByRole("alert"));
    await expect(
      alert.getByText("Astrolift kept this zone: it is the default for new app hostnames.")
    ).toBeVisible();
    await expect(alert.getByText(DELETE_REFUSAL.message)).toBeVisible();
    await expect(dialog.getByText("Environments that resolve through it: 3")).toBeVisible();
  },
};

/** A removal the server did not accept leaves the dialog open, armed for a retry. */
export const RemovalNotAccepted: Story = {
  render: () => <Demo onConfirm={async () => false} />,
  play: async ({ canvasElement }) => {
    const page = within(canvasElement.ownerDocument.body);
    const dialog = within(await page.findByRole("alertdialog"));
    await userEvent.type(
      dialog.getByLabelText(`To confirm, type the zone: ${DOMAIN_DEFAULT.zone}`),
      DOMAIN_DEFAULT.zone
    );
    await userEvent.click(dialog.getByRole("button", { name: "Remove default zone" }));
    await waitFor(() =>
      expect(dialog.getByRole("button", { name: "Remove default zone" })).toBeEnabled()
    );
    await expect(page.getByRole("alertdialog")).toBeVisible();
  },
};

const LONG_DEPENDENTS: ZoneDependent[] = [
  {
    organizationSlug: `${LONG}-organization`,
    appSlug: LONG,
    environmentName: `${LONG}-env`,
    url: `https://${LONG}.${LONG}.apps.acme.example`,
  },
];

export const LongStrings: Story = {
  render: () => (
    <Demo
      zone={`${LONG}.apps.acme.example`}
      dependents={LONG_DEPENDENTS}
      total={1}
      refusal={`${LONG}.apps.acme.example is the effective default zone for new app hostnames. 1 environment resolves through it: ${LONG}-organization/${LONG}/${LONG}-env.`}
    />
  ),
};
