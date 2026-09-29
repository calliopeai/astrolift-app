import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PlaygroundBatch } from "./PlaygroundBatch";
import { PlaygroundScreen } from "./PlaygroundScreen";
import { BATCH, LONG, PLAYGROUND } from "./playground.fixtures";

const meta: Meta = {
  title: "Screens/Playground/PlaygroundScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const batch = <PlaygroundBatch {...BATCH} />;

export const Full: Story = {
  render: () => <PlaygroundScreen {...PLAYGROUND} batch={batch} />,
};

/** A prompt was sent and the simulated reply is pending. */
export const Loading: Story = {
  render: () => <PlaygroundScreen {...PLAYGROUND} loading batch={batch} />,
};

/** Fresh session: nothing saved, no conversation yet. */
export const Empty: Story = {
  render: () => (
    <PlaygroundScreen
      {...PLAYGROUND}
      title=""
      messages={[]}
      savedSessions={[]}
      activeSavedId={null}
      batch={batch}
    />
  ),
};

/** The chat tab has no error state; the batch tab's failed result is the closest. */
export const BatchTab: Story = {
  render: () => (
    <PlaygroundScreen
      {...PLAYGROUND}
      tab="batch"
      batch={
        <PlaygroundBatch
          {...BATCH}
          results={[{ input: BATCH.inputs[0], output: "", ok: false, error: "Model unavailable" }]}
        />
      }
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PlaygroundScreen
      {...PLAYGROUND}
      title={LONG}
      prompt={LONG}
      messages={[
        { role: "user", content: LONG },
        { role: "assistant", content: `[Genesis] This is a simulated response to: "${LONG}"` },
      ]}
      savedSessions={[{ ...PLAYGROUND.savedSessions[0], title: LONG }]}
      batch={batch}
    />
  ),
};
