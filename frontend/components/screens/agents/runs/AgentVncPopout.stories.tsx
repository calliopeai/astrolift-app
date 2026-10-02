import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentVncPopoutScreen } from "./AgentVncPopout";
import { VNC_POPOUT } from "./agent-runs.fixtures";

/** In the catalog there is no VNC relay, so the live state shows the viewer's disconnected chrome. */
const meta: Meta = {
  title: "Screens/Agents/Runs/AgentVncPopout",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Live: Story = { render: () => <AgentVncPopoutScreen {...VNC_POPOUT} /> };

export const Loading: Story = {
  render: () => <AgentVncPopoutScreen {...VNC_POPOUT} task={null} loading />,
};

/** No task with this id (the closest thing to empty). */
export const NotFound: Story = {
  render: () => <AgentVncPopoutScreen {...VNC_POPOUT} task={null} />,
};

export const SessionEnded: Story = {
  render: () => (
    <AgentVncPopoutScreen
      {...VNC_POPOUT}
      task={{ ...VNC_POPOUT.task!, status: "completed", vncUrl: "" }}
    />
  ),
};

export const LoadError: Story = {
  render: () => (
    <AgentVncPopoutScreen
      {...VNC_POPOUT}
      task={null}
      error="Response not successful: Received status code 502"
    />
  ),
};

export const LongTaskId: Story = {
  render: () => (
    <AgentVncPopoutScreen
      {...VNC_POPOUT}
      taskId={"a-very-long-task-identifier-that-keeps-going-".repeat(4)}
      task={{ ...VNC_POPOUT.task!, status: "running", vncEnabled: false }}
    />
  ),
};

export const FrenchSessionEnded: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <AgentVncPopoutScreen
        {...VNC_POPOUT}
        task={{ ...VNC_POPOUT.task!, status: "completed", vncUrl: "" }}
      />
    </NextIntlClientProvider>
  ),
};
export const JapaneseUnavailable: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <AgentVncPopoutScreen {...VNC_POPOUT} task={{ ...VNC_POPOUT.task!, vncEnabled: false }} />
    </NextIntlClientProvider>
  ),
};
