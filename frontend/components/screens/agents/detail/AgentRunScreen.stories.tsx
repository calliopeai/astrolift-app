import { NextIntlClientProvider, useTranslations } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";
import { LiveLogTerminal } from "@/components/observability/LiveLogTerminal";

import {
  LOG_LINES,
  RUN,
  RUN_EMPTY,
  RUN_ERROR,
  RUN_LOADING,
  RUN_LONG,
  RUN_NEW_ROWS,
  type RunData,
} from "./agent-build-run.fixtures";
import { localizedAgentRunsList } from "./agent-runs-list";
import { AgentRunScreen } from "./AgentRunScreen";

/**
 * The agent's Runs tab (spec 44 §5.1, §5.2): this agent's runs on the
 * embedded list, cursor paged, newest first. Run now is the frame's.
 */
const meta: Meta = {
  title: "Screens/Agents/Detail/AgentRunScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const renderLogs = (taskId: string, running: boolean) => (
  <LiveLogTerminal
    taskId={taskId}
    running={running}
    lines={LOG_LINES}
    error={null}
    loading={false}
    className="min-h-0 flex-1"
  />
);

function Runs({ data, view }: { data: RunData; view?: string }) {
  const t = useTranslations("agentRunTab");
  const activity = useTranslations("agentActivity");
  const list = useLocalListState(localizedAgentRunsList(t, activity), view ? { view } : {});
  return <AgentRunScreen {...data} list={list} renderLogs={renderLogs} />;
}

/** Every run state: a VNC run (Watch live), a headless run (Watch logs), queued and finished runs. */
export const Full: Story = {
  render: () => <Runs data={RUN} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByText("Running now").length).toBe(2);
    await expect(canvas.getByText("Timed out")).toBeInTheDocument();
  },
};

export const Loading: Story = { render: () => <Runs data={RUN_LOADING} /> };

export const Empty: Story = {
  render: () => <Runs data={RUN_EMPTY} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("No runs yet")).toBeInTheDocument();
  },
};

export const Error: Story = {
  render: () => <Runs data={RUN_ERROR} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  },
};

/** Mine says what it covers: a run does not record who started it yet. */
export const MineView: Story = {
  render: () => <Runs data={RUN} view="mine" />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText(/Mine shows every run/)).toBeInTheDocument();
  },
};

/** New runs arrived while reading: they wait behind the pill. */
export const NewRows: Story = { render: () => <Runs data={RUN_NEW_ROWS} /> };

export const LongStrings: Story = { render: () => <Runs data={RUN_LONG} /> };

/** The narrowest the web console goes: the table scrolls in its own frame. */
export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Runs data={RUN_LONG} />
    </div>
  ),
};

/** Opening a headless run's log tail from its row menu. */
export const WatchLogs: Story = {
  render: () => <Runs data={RUN} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const body = within(canvasElement.ownerDocument.body);
    const menus = canvas.getAllByRole("button", { name: "Runs: row actions" });
    await userEvent.click(menus[1]!);
    await userEvent.click(await body.findByRole("menuitem", { name: /watch logs/i }));
    await expect(await body.findByText("Live agent logs")).toBeInTheDocument();
  },
};

export const FrenchRuns: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <Runs data={RUN} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseMine: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <Runs data={RUN_EMPTY} view="mine" />
    </NextIntlClientProvider>
  ),
};
export const JapaneseUnknownRun: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <Runs
        data={{
          ...RUN,
          rows: [
            {
              ...RUN.rows[0],
              status: "future_status_v2",
              startedAt: "malformed",
              finishedAt: null,
            },
          ],
          totalCount: 1,
        }}
      />
    </NextIntlClientProvider>
  ),
};
