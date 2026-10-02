import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  LiveLogTerminal,
  type LiveLogTerminalProps,
} from "@/components/observability/LiveLogTerminal";

const meta: Meta = { title: "Patterns/Observability/LiveLogTerminal" };
export default meta;

type Story = StoryObj;

const LINES = [
  "2026-09-28T12:00:01Z INFO  agent starting: bdr-outreach v0.1.37",
  "2026-09-28T12:00:02Z INFO  loaded 14 tools from the skill bundle",
  "2026-09-28T12:00:04Z INFO  fetching 25 accounts from the CRM",
  "2026-09-28T12:00:09Z WARN  rate limited by provider, retrying in 2s",
  "2026-09-28T12:00:12Z INFO  drafted 3 messages; routing to the approval gate",
  '2026-09-28T12:00:12Z INFO  a very long line that keeps going to show how the terminal wraps output which carries a full JSON payload {"account":"acme","score":0.82,"reasons":["hiring","funding"]}',
];

const base: LiveLogTerminalProps = {
  taskId: "7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b",
  running: true,
  lines: LINES,
  error: null,
  loading: false,
  className: "h-[20rem]",
};

export const Tailing: Story = { render: () => <LiveLogTerminal {...base} /> };

export const WaitingForOutput: Story = {
  render: () => <LiveLogTerminal {...base} lines={null} loading />,
};

export const NoOutputRecorded: Story = {
  render: () => <LiveLogTerminal {...base} running={false} lines={[]} />,
};

export const LoadError: Story = {
  render: () => <LiveLogTerminal {...base} lines={null} error="Response not successful: 504" />,
};

export const FrenchWaiting: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <LiveLogTerminal {...base} lines={null} loading />
    </NextIntlClientProvider>
  ),
};
export const JapaneseUnavailable: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <LiveLogTerminal {...base} running={false} lines={[]} />
    </NextIntlClientProvider>
  ),
};
export const FrenchReadError: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <LiveLogTerminal {...base} lines={null} error="RAW_LOG_DIAGNOSTIC" />
    </NextIntlClientProvider>
  ),
};
