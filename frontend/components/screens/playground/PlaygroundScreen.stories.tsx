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

/** A prompt was sent and the actual relay reply is pending. */
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

/** A failed real prompt row appears in the batch tab. */
export const BatchTab: Story = {
  render: () => (
    <PlaygroundScreen
      {...PLAYGROUND}
      tab="batch"
      batch={
        <PlaygroundBatch
          {...BATCH}
          results={[{ input: BATCH.inputs[0], output: "", ok: false, error: "unavailable" }]}
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
        { role: "assistant", content: LONG },
      ]}
      savedSessions={[{ ...PLAYGROUND.savedSessions[0], title: LONG }]}
      batch={batch}
    />
  ),
};

export const CheckingEndpoint: Story = {
  render: () => (
    <PlaygroundScreen {...PLAYGROUND} readiness="loading" canSend={false} batch={batch} />
  ),
};
export const Disconnected: Story = {
  render: () => (
    <PlaygroundScreen {...PLAYGROUND} readiness="STALE_HEARTBEAT" canSend={false} batch={batch} />
  ),
};
export const Refused: Story = {
  render: () => (
    <PlaygroundScreen {...PLAYGROUND} readiness="refused" canSend={false} batch={batch} />
  ),
};
export const Unsupported: Story = {
  render: () => (
    <PlaygroundScreen {...PLAYGROUND} readiness="UNSUPPORTED" canSend={false} batch={batch} />
  ),
};
export const TimedOut: Story = {
  render: () => <PlaygroundScreen {...PLAYGROUND} error="timedOut" batch={batch} />,
};
export const NoVisibleEndpoints: Story = {
  render: () => (
    <PlaygroundScreen
      {...PLAYGROUND}
      models={[]}
      totalCount={0}
      model=""
      modelName=""
      readiness="unselected"
      canSend={false}
      batch={batch}
    />
  ),
};
export const CatalogError: Story = {
  render: () => <PlaygroundScreen {...PLAYGROUND} catalogError canSend={false} batch={batch} />,
};
