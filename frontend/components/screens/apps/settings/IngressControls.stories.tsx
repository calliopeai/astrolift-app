import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import es from "@/messages/es.json";

import { ENVIRONMENTS, INGRESS, LONG } from "./app-settings-members.fixtures";
import { IngressControlsView } from "./IngressControls";

const meta: Meta<typeof IngressControlsView> = {
  title: "Screens/Apps/Settings/IngressControls",
  component: IngressControlsView,
  args: INGRESS,
};
export default meta;

type Story = StoryObj<typeof IngressControlsView>;

export const Full: Story = {};

export const Loading: Story = { args: { envs: [], loading: true } };

export const Empty: Story = { args: { envs: [] } };

/** No error state; a failed toggle is a toast. Shown: a toggle in flight. */
export const Toggling: Story = { args: { busyIds: [ENVIRONMENTS[0].id] } };

export const LongStrings: Story = {
  args: { envs: [{ ...ENVIRONMENTS[0], name: LONG }, ENVIRONMENTS[1]] },
};

export const SpanishWidth768: Story = {
  render: (args) => (
    <NextIntlClientProvider locale="es" messages={es} timeZone="UTC">
      <div style={{ width: 768 }}>
        <IngressControlsView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
