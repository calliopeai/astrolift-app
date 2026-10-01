import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import { RETENTION } from "./app-settings-members.fixtures";
import { RetentionPolicyView } from "./RetentionPolicy";

const meta: Meta<typeof RetentionPolicyView> = {
  title: "Screens/Apps/Settings/RetentionPolicy",
  component: RetentionPolicyView,
  args: RETENTION,
};
export default meta;

type Story = StoryObj<typeof RetentionPolicyView>;

export const Full: Story = {};

/** A save in flight for one signal. */
export const Loading: Story = { args: { saving: { logs: true } } };

/** No policies: every signal on the platform default. */
export const Empty: Story = { args: { policies: [] } };

/** No error state; a failed save is a toast. Shown: every signal overridden. */
export const AllSet: Story = {
  args: {
    policies: [
      { id: "rp-1", signal: "logs", retentionDays: 7 },
      { id: "rp-2", signal: "metrics", retentionDays: 90 },
      { id: "rp-3", signal: "traces", retentionDays: 14 },
      { id: "rp-4", signal: "audit_events", retentionDays: 365 },
    ],
  },
};

/** Signal labels are fixed; no user text reaches this section. Shown: the longest values. */
export const LongStrings: Story = {
  args: { policies: [{ id: "rp-1", signal: "audit_events", retentionDays: 365 }] },
};

export const French: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="Europe/Paris">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};

export const JapaneseDefaults: Story = {
  args: { policies: [] },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="Asia/Tokyo">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};

/** A recorded period outside this card's fixed presets remains visible. */
export const RecordedCustomPeriod: Story = {
  args: { policies: [{ id: "rp-custom", signal: "logs", retentionDays: 45 }] },
};
