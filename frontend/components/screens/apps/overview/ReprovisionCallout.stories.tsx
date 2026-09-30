import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import french from "@/messages/fr.json";
import { expect, userEvent, within } from "storybook/test";

import {
  LONG,
  REPROVISION,
  REPROVISION_FAILED,
  REPROVISION_IN_FLIGHT,
} from "./app-overview-banners.fixtures";
import { ReprovisionCalloutView } from "./ReprovisionCallout";

const meta: Meta = { title: "Screens/Apps/Overview/ReprovisionCallout" };
export default meta;

type Story = StoryObj;

/** Registry missing (amber) with the reprovision CTA. */
export const Full: Story = {
  render: () => (
    <ReprovisionCalloutView
      {...REPROVISION}
      reprovision={{ ...REPROVISION_FAILED, state: "ready_missing_registry", elapsedSeconds: 95 }}
    />
  ),
};

/** Provisioning in flight: no CTA, elapsed time shown. */
export const Loading: Story = {
  render: () => <ReprovisionCalloutView {...REPROVISION} reprovision={REPROVISION_IN_FLIGHT} />,
};

/** Nothing to reprovision: the callout renders nothing. */
export const Empty: Story = {
  render: () => (
    <div data-testid="slot">
      <ReprovisionCalloutView
        {...REPROVISION}
        reprovision={{ ...REPROVISION_FAILED, needsReprovision: false }}
      />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("slot")).toBeEmptyDOMElement();
  },
};

/** Provisioning failed (red); the CTA opens the confirm dialog. */
export const ErrorState: Story = {
  render: () => <ReprovisionCalloutView {...REPROVISION} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /reprovision/i }));
    await expect(
      await within(document.body).findByRole("alertdialog", { name: /reprovision checkout/i })
    ).toBeInTheDocument();
  },
};

/** Reprovision dispatched: the CTA is busy. */
export const Redeploying: Story = {
  render: () => <ReprovisionCalloutView {...REPROVISION} redeploying />,
};

export const LongStrings: Story = {
  render: () => (
    <ReprovisionCalloutView
      {...REPROVISION}
      appSlug={LONG}
      reprovision={{ ...REPROVISION_FAILED, state: LONG, reason: `${LONG} ${LONG}` }}
    />
  ),
};

export const FrenchUnknown: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={french}>
      <ReprovisionCalloutView
        {...REPROVISION}
        reprovision={{ ...REPROVISION_FAILED, state: "future_state", reason: "" }}
      />
    </NextIntlClientProvider>
  ),
};
