import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SecretHistoryPanelView } from "./SecretHistoryPanel";
import { HISTORY, LONG } from "./app-secrets-tokens.fixtures";

const meta: Meta<typeof SecretHistoryPanelView> = {
  title: "Screens/Apps/Secrets/SecretHistoryPanel",
  component: SecretHistoryPanelView,
  args: HISTORY,
  decorators: [
    (Story) => (
      <div className="bg-background w-96 rounded-md border">
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof SecretHistoryPanelView>;

export const Full: Story = {};

export const Loading: Story = { args: { entries: [], loading: true } };

export const Empty: Story = { args: { entries: [] } };

export const LoadFailed: Story = { args: { entries: [], error: true } };

export const LongStrings: Story = {
  args: {
    secretKey: LONG.toUpperCase(),
    entries: HISTORY.entries.map((e) => ({
      ...e,
      action: `app.secret.${LONG}`,
      actor: { id: "u-long", username: LONG },
      sourceIp: "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
    })),
  },
};

/** Narrow is the sheet's normal width; 768px is the widest it gets beside the table. */
export const W768: Story = {
  args: LongStrings.args,
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};

/** A long history: grouped by day, scrolling in its own frame. */
export const LongHistory: Story = {
  args: {
    entries: Array.from({ length: 40 }, (_, i) => ({
      ...HISTORY.entries[i % HISTORY.entries.length]!,
      timestamp: new Date(Date.UTC(2026, 8, 28, 12) - i * 6 * 3_600_000).toISOString(),
    })),
  },
};

export const German: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
