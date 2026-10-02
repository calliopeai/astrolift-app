import * as React from "react";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentInteractionMapView } from "./AgentInteractionMap";
import { INTERACTION_MAP, LONG_INTERACTIONS } from "./agent-runs.fixtures";

const meta: Meta = { title: "Screens/Agents/Runs/AgentInteractionMap" };
export default meta;

type Story = StoryObj;

export const Live: Story = { render: () => <AgentInteractionMapView {...INTERACTION_MAP} /> };

export const Settled: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} taskStatus="completed" isTerminal />,
};

export const Loading: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} interactions={[]} loading />,
};

export const Empty: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} interactions={[]} />,
};

export const LoadError: Story = {
  render: () => (
    <AgentInteractionMapView
      {...INTERACTION_MAP}
      interactions={[]}
      error="Response not successful: Received status code 502"
    />
  ),
};

export const LongNames: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} interactions={LONG_INTERACTIONS} />,
};

export const FrenchLive: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <AgentInteractionMapView {...INTERACTION_MAP} />
    </NextIntlClientProvider>
  ),
};

export const JapaneseEmpty: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <AgentInteractionMapView {...INTERACTION_MAP} interactions={[]} />
    </NextIntlClientProvider>
  ),
};

function LocaleSwitchFixture() {
  const [locale, setLocale] = React.useState<"fr" | "ja">("fr");
  return (
    <NextIntlClientProvider locale={locale} messages={locale === "fr" ? fr : ja} timeZone="UTC">
      <button type="button" onClick={() => setLocale("ja")}>
        日本語
      </button>
      <AgentInteractionMapView {...INTERACTION_MAP} taskStatus="future_status_v2" />
    </NextIntlClientProvider>
  );
}
export const LocaleSwitch: Story = { render: () => <LocaleSwitchFixture /> };
