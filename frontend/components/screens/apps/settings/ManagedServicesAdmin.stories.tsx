import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import spanish from "@/messages/es.json";

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

/** Unknown reads stay visible; confirmed empty inventories retain the hidden section. */
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

export const ReadError: Story = {
  args: { services: [], error: "RAW_MANAGED_READ_DIAGNOSTIC", onRetry: async () => {} },
};
export const CachedReadError: Story = {
  args: { error: "RAW_MANAGED_READ_DIAGNOSTIC", onRetry: async () => {} },
};
export const WildcardTypedConfig: Story = {
  args: {
    services: [
      {
        ...SERVICES[0],
        editableFields: ["*"],
        config: {
          capacity: 4,
          enabled: true,
          region: "REGION_LITERAL",
          nested: { mode: "MODE_LITERAL" },
        },
      },
    ],
  },
};
export const PendingReprovision: Story = { args: { reprovisioning: true } };
export const PendingUpdate: Story = { args: { updating: true } };
export const UnknownStatus: Story = {
  args: { services: [{ ...SERVICES[0], status: "constructor" }] },
};
export const SpanishWidth768: Story = {
  render: (args) => (
    <NextIntlClientProvider locale="es" messages={spanish}>
      <div style={{ width: 768 }}>
        <ManagedServicesAdminView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
