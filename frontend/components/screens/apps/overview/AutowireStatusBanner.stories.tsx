import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import { expect, within } from "storybook/test";

import {
  AUTOWIRE,
  AUTOWIRE_FAILING,
  AUTOWIRE_NOT_CONNECTED,
  AUTOWIRE_WIRED,
  LONG,
} from "./app-overview-banners.fixtures";
import { AutowireStatusBannerView } from "./AutowireStatusBanner";

const meta: Meta = { title: "Screens/Apps/Overview/AutowireStatusBanner" };
export default meta;

type Story = StoryObj;

/** Connected, with failing steps: the repair banner. */
export const Full: Story = {
  render: () => <AutowireStatusBannerView {...AUTOWIRE} />,
};

/** Retry in flight. */
export const Loading: Story = {
  render: () => <AutowireStatusBannerView {...AUTOWIRE} retrying />,
};

/** Fully wired: the banner renders nothing. */
export const Empty: Story = {
  render: () => (
    <div data-testid="slot">
      <AutowireStatusBannerView {...AUTOWIRE} autowire={AUTOWIRE_WIRED} />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("slot")).toBeEmptyDOMElement();
  },
};

/** Not connected: the "connect for auto-deploy" callout. */
export const NotConnected: Story = {
  render: () => <AutowireStatusBannerView {...AUTOWIRE} autowire={AUTOWIRE_NOT_CONNECTED} />,
};

/** Every step failed, one with a status the copy table does not know. */
export const ErrorState: Story = {
  render: () => (
    <AutowireStatusBannerView
      {...AUTOWIRE}
      autowire={{ ...AUTOWIRE_FAILING, ciWorkflow: "error", webhook: "rate_limited" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AutowireStatusBannerView
      {...AUTOWIRE}
      sourceRepo={`acme/${LONG}`}
      autowire={{ ...AUTOWIRE_FAILING, detail: `${LONG} ${LONG} ${LONG}` }}
    />
  ),
};

export const FrenchRecovery: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <AutowireStatusBannerView
        {...AUTOWIRE}
        autowire={{
          ...AUTOWIRE_FAILING,
          webhook: "rate_limited",
          detail: "RAW_PROVIDER_DIAGNOSTIC",
        }}
      />
    </NextIntlClientProvider>
  ),
};

export const JapaneseConnect: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <AutowireStatusBannerView {...AUTOWIRE} autowire={AUTOWIRE_NOT_CONNECTED} />
    </NextIntlClientProvider>
  ),
};
