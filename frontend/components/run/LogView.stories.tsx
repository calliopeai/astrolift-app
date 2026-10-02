import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { FAILED_LINES, LONG_LINES, makeLines, RUN_LINES } from "./fixtures";
import { LogView, type LogLevel } from "./LogView";

/** The one log pane (spec 44 §5.5): follows the end until the reader scrolls up. */
const meta: Meta = { title: "Run/LogView", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <LogView lines={RUN_LINES} onDownload={() => {}} /> };

/** New lines arrive every half second; scroll up to stop following. */
export const Live: Story = {
  render: () => {
    function Live() {
      const [lines, setLines] = React.useState(() => makeLines(20));
      React.useEffect(() => {
        if (lines.length >= 200) return;
        const t = setTimeout(() => setLines((l) => [...l, ...makeLines(3, l.length)]), 500);
        return () => clearTimeout(t);
      }, [lines]);
      return <LogView lines={lines} onDownload={() => {}} />;
    }
    return <Live />;
  },
};

/** A finished, failed run reads the same, opening at the end. */
export const Failed: Story = {
  render: () => <LogView lines={FAILED_LINES} onDownload={() => {}} />,
};

/** 5000 lines, chunked so off-screen lines skip layout. */
export const FiveThousandLines: Story = {
  render: () => <LogView lines={makeLines(5000)} onDownload={() => {}} />,
};

export const Loading: Story = { render: () => <LogView lines={[]} loading /> };
export const Empty: Story = {
  render: () => <LogView lines={[]} emptyHint="Waiting for the first line." />,
};
export const ErrorState: Story = {
  render: () => (
    <LogView lines={[]} error="log stream closed: 502 from agent gateway" onRetry={() => {}} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <LogView lines={LONG_LINES} title={`Log ${LONG_LINES[0].message}`} onDownload={() => {}} />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <LogView lines={[...LONG_LINES, ...RUN_LINES]} onDownload={() => {}} />
    </div>
  ),
};

export const FrenchDefault: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <LogView lines={[]} onDownload={() => {}} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseLog: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <LogView
        lines={[{ ts: "2026-10-02T00:00:00Z", message: "RAW_LOG_BODY", level: "warn" }]}
        onDownload={() => {}}
      />
    </NextIntlClientProvider>
  ),
};

export const JapaneseUnknownWireLevel: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <LogView
        lines={[
          {
            ts: "2026-10-02T00:00:00Z",
            message: "RAW_FUTURE_LEVEL_BODY",
            level: "future_level_v2" as LogLevel,
          },
        ]}
      />
    </NextIntlClientProvider>
  ),
};
