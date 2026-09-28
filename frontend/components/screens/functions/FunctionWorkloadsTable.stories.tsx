import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  FUNCTION_WORKLOADS,
  FUNCTION_WORKLOADS_EMPTY,
  FUNCTION_WORKLOADS_ERROR,
  FUNCTION_WORKLOADS_LOADING,
  FUNCTION_WORKLOADS_LONG,
} from "../fleet/fleet-functions-logs.fixtures";

import { FunctionWorkloadsTable } from "./FunctionWorkloadsTable";

const meta: Meta = {
  title: "Screens/Functions/FunctionWorkloadsTable",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <FunctionWorkloadsTable {...FUNCTION_WORKLOADS} /> };

export const Loading: Story = {
  render: () => <FunctionWorkloadsTable {...FUNCTION_WORKLOADS_LOADING} />,
};

/** No function on this page; Next stays enabled (the kind filter is client-side). */
export const Empty: Story = {
  render: () => <FunctionWorkloadsTable {...FUNCTION_WORKLOADS_EMPTY} />,
};

export const LoadFailed: Story = {
  render: () => <FunctionWorkloadsTable {...FUNCTION_WORKLOADS_ERROR} />,
};

export const LongStrings: Story = {
  render: () => <FunctionWorkloadsTable {...FUNCTION_WORKLOADS_LONG} />,
};
