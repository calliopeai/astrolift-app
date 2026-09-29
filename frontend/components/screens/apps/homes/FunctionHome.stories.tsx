import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { FUNCTION, GOLDEN_SIGNALS, LONG } from "./app-homes.fixtures";
import { FunctionHomeScreen } from "./FunctionHome";

const meta: Meta = {
  title: "Screens/Apps/Homes/FunctionHome",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <FunctionHomeScreen {...FUNCTION} /> };

export const Loading: Story = {
  render: () => (
    <FunctionHomeScreen
      {...FUNCTION}
      goldenSignals={{
        ...GOLDEN_SIGNALS,
        signals: null,
        reason: null,
        loading: true,
        statusLoading: true,
      }}
    />
  ),
};

/** Event-triggered, and no invocations yet. */
export const Empty: Story = {
  render: () => (
    <FunctionHomeScreen
      {...FUNCTION}
      workload={{ isPublic: false }}
      host={null}
      goldenSignals={{
        ...GOLDEN_SIGNALS,
        reason: "NO_DATA_YET",
        signals: (GOLDEN_SIGNALS.signals ?? []).map((s) => ({
          ...s,
          reason: "NO_DATA_YET",
          samples: [],
        })),
      }}
    />
  ),
};

export const SignalsError: Story = {
  render: () => (
    <FunctionHomeScreen
      {...FUNCTION}
      goldenSignals={{ ...GOLDEN_SIGNALS, reason: "ERROR", signals: [] }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <FunctionHomeScreen {...FUNCTION} name={LONG} host={`${LONG}.apps.example.com`} />,
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <FunctionHomeScreen {...FUNCTION} name={LONG} host={`${LONG}.apps.example.com`} />
    </div>
  ),
};
