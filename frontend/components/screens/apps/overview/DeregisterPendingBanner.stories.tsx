import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import es from "@/messages/es.json";

import { DEREGISTER } from "./app-overview-banners.fixtures";
import { DeregisterPendingBannerView } from "./DeregisterPendingBanner";

const meta: Meta = { title: "Screens/Apps/Overview/DeregisterPendingBanner" };
export default meta;

type Story = StoryObj;

/** A grace window with 4:32 left. */
export const Full: Story = {
  render: () => <DeregisterPendingBannerView {...DEREGISTER} />,
};

/** Cancel in flight. */
export const Loading: Story = {
  render: () => <DeregisterPendingBannerView {...DEREGISTER} cancelling />,
};

/** Nothing pending: the banner renders nothing. */
export const Empty: Story = {
  render: () => (
    <div data-testid="slot">
      <DeregisterPendingBannerView {...DEREGISTER} msRemaining={null} />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("slot")).toBeEmptyDOMElement();
  },
};

/** Failures surface as toasts; the closest in-banner state is the last second. */
export const ErrorState: Story = {
  render: () => <DeregisterPendingBannerView {...DEREGISTER} msRemaining={900} />,
};

/** The copy is fixed; the longest countdown is the full five minutes. */
export const LongStrings: Story = {
  render: () => <DeregisterPendingBannerView {...DEREGISTER} msRemaining={300_000} />,
};

export const SpanishWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="es" messages={es}>
      <div style={{ width: 768 }}>
        <DeregisterPendingBannerView {...DEREGISTER} />
      </div>
    </NextIntlClientProvider>
  ),
};
