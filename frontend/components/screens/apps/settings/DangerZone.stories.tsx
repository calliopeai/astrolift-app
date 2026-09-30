import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";

import { DANGER_ZONE, DEREGISTER_PREVIEW, LONG } from "./app-settings-members.fixtures";
import { DangerZoneView } from "./DangerZone";

const meta: Meta<typeof DangerZoneView> = {
  title: "Screens/Apps/Settings/DangerZone",
  component: DangerZoneView,
  args: DANGER_ZONE,
};
export default meta;

type Story = StoryObj<typeof DangerZoneView>;

async function openDialog(canvasElement: HTMLElement) {
  await userEvent.click(within(canvasElement).getByRole("button", { name: /deregister/i }));
  await expect(await within(document.body).findByRole("alertdialog")).toBeInTheDocument();
}

/** The card, closed, with the resource-count badge. */
export const Card: Story = { args: { preview: DEREGISTER_PREVIEW } };

/** The confirm dialog with the blast-radius preview. */
export const Full: Story = {
  args: { preview: DEREGISTER_PREVIEW },
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

export const Loading: Story = {
  args: { previewLoading: true },
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

export const Unavailable: Story = {
  args: { previewError: new Error("The current credential cannot preview this app.") },
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

export const Empty: Story = {
  args: {
    preview: {
      ...DEREGISTER_PREVIEW,
      totalResourceCount: 0,
      k8sObjects: [],
      managedServices: [],
      secretRefs: [],
      deployTokens: [],
      identityRoles: [],
      sourceWebhook: null,
      registryRepoUri: "",
    },
  },
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

/** A partial teardown: resources still live, with the retry CTA. */
export const StillLive: Story = {
  args: {
    stillLive: ["prd-us-west-2/checkout/Deployment/web", "postgres/orders-db"],
  },
};

export const LongStrings: Story = {
  args: {
    appName: LONG,
    stillLive: [LONG, `${LONG}-2`],
  },
};

export const Width768: Story = {
  args: { appName: LONG, stillLive: [LONG, `${LONG}-2`], preview: DEREGISTER_PREVIEW },
  render: (args) => (
    <div style={{ width: 768 }}>
      <DangerZoneView {...args} />
    </div>
  ),
};

export const FrenchWidth768: Story = {
  args: { preview: DEREGISTER_PREVIEW },
  render: (args) => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <div style={{ width: 768 }}>
        <DangerZoneView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Désenregistrer/ }));
    await expect(await within(document.body).findByRole("alertdialog")).toBeInTheDocument();
  },
};
