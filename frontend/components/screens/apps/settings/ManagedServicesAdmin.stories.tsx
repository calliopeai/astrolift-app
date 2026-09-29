import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { LONG, MANAGED_SERVICES_ADMIN, SERVICES } from "./app-settings-members.fixtures";
import { ManagedServicesAdminView } from "./ManagedServicesAdmin";

const meta: Meta<typeof ManagedServicesAdminView> = {
  title: "Screens/Apps/Settings/ManagedServicesAdmin",
  component: ManagedServicesAdminView,
  args: MANAGED_SERVICES_ADMIN,
};
export default meta;

type Story = StoryObj<typeof ManagedServicesAdminView>;

/** Active, provisioning (actions disabled) and failed services. */
export const Full: Story = {};

/**
 * The section hides while loading and when there are no live services, so
 * the Loading and Empty stories render nothing inside the frame.
 */
export const Loading: Story = { args: { services: [], loading: true } };

export const Empty: Story = { args: { services: [] } };

/** A failed service carries its status error inline (the error state). */
export const Failed: Story = { args: { services: [SERVICES[2]] } };

/** The edit sheet for a service with hot-swappable fields. */
export const EditSheet: Story = {
  args: { services: [SERVICES[0]] },
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Edit/ }));
    await expect(await within(document.body).findByRole("dialog")).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  args: {
    services: [{ ...SERVICES[2], name: LONG, environmentName: LONG, statusError: LONG }],
  },
};
