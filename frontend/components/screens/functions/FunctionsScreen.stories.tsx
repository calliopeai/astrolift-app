import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  FUNCTION_WORKLOADS,
  FUNCTION_WORKLOADS_EMPTY,
  FUNCTION_WORKLOADS_ERROR,
  FUNCTION_WORKLOADS_LOADING,
  FUNCTION_WORKLOADS_LONG,
  FUNCTIONS_TAB,
} from "../fleet/fleet-functions-logs.fixtures";

import { FunctionsScreen } from "./FunctionsScreen";
import { FunctionWorkloadsTable } from "./FunctionWorkloadsTable";

const meta: Meta = {
  title: "Screens/Functions/FunctionsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => (
    <FunctionsScreen
      {...FUNCTIONS_TAB}
      fleet={<FunctionWorkloadsTable {...FUNCTION_WORKLOADS} />}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <FunctionsScreen
      {...FUNCTIONS_TAB}
      fleet={<FunctionWorkloadsTable {...FUNCTION_WORKLOADS_LOADING} />}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <FunctionsScreen
      {...FUNCTIONS_TAB}
      fleet={<FunctionWorkloadsTable {...FUNCTION_WORKLOADS_EMPTY} />}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <FunctionsScreen
      {...FUNCTIONS_TAB}
      fleet={<FunctionWorkloadsTable {...FUNCTION_WORKLOADS_ERROR} />}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <FunctionsScreen
      {...FUNCTIONS_TAB}
      fleet={<FunctionWorkloadsTable {...FUNCTION_WORKLOADS_LONG} />}
    />
  ),
};

/** A signal tab: a gateway placeholder until the function runtime ships. */
export const ThroughputTab: Story = {
  render: () => <FunctionsScreen {...FUNCTIONS_TAB} tab="throughput" fleet={null} />,
};

export const LatencyTab: Story = {
  render: () => <FunctionsScreen {...FUNCTIONS_TAB} tab="latency" fleet={null} />,
};
