import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";

import { BOTH, homeProps, NO_ACCESS, ONLY_APPS, OPERATOR } from "@/components/home/fixtures";
import type { HomeAccess, HomeLayoutKey } from "@/components/home/registry";

import { HomeLayoutSettings } from "./HomeLayoutSettings";

/** Settings › Home: the Home layout, saved as it is chosen. */
const meta: Meta = { title: "Screens/Settings/Account/HomeLayoutSettings" };
export default meta;

type Story = StoryObj;

function Live({
  access,
  saved,
  loading,
}: {
  access: HomeAccess;
  saved: HomeLayoutKey | null;
  loading?: boolean;
}) {
  const [savedLayout, setSaved] = React.useState(saved);
  const props = homeProps(access, savedLayout);
  return (
    <HomeLayoutSettings
      loading={loading}
      layouts={props.layouts}
      layout={props.layout}
      savedLayout={savedLayout}
      defaultLayout={props.defaultLayout}
      onLayoutChange={setSaved}
      onResetLayout={() => setSaved(null)}
    />
  );
}

/** Unset: Home draws the access default. */
export const Default: Story = { render: () => <Live access={BOTH} saved={null} /> };

/** A saved choice that differs from the default offers the way back. */
export const SavedChoice: Story = { render: () => <Live access={BOTH} saved="apps" /> };

export const Operator: Story = { render: () => <Live access={OPERATOR} saved="builder" /> };

export const OnlyApps: Story = { render: () => <Live access={ONLY_APPS} saved={null} /> };

export const Loading: Story = { render: () => <Live access={BOTH} saved={null} loading /> };

export const NoAccess: Story = { render: () => <Live access={NO_ACCESS} saved={null} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Live access={OPERATOR} saved="agents" />
    </div>
  ),
};

export const GermanSavedChoice768: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => (
    <div style={{ width: 768 }}>
      <Live access={OPERATOR} saved="agents" />
    </div>
  ),
};

export const JapaneseNoAccess: Story = {
  ...NoAccess,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
