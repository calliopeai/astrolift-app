import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { NitroTasksView, type NitroTasksProps } from "./NitroTasks";

const props: NitroTasksProps = {
  loading: false,
  saving: false,
  onRefresh: () => undefined,
  onChange: async () => true,
  readiness: {
    clusterId: "cluster",
    version: 1,
    sourceToken: "reviewed",
    checkedAt: "2026-11-10T12:00:00Z",
    namespace: "astrolift-agents-example",
    source: { region: "us-west-2", configurationScope: "current_process" },
    modes: [
      {
        mode: "isolated",
        enabled: false,
        state: "ready",
        checks: [
          {
            code: "runtime_class",
            state: "ready",
            message: "The isolated RuntimeClass is available.",
            evidence: {},
          },
        ],
      },
      {
        mode: "confidential",
        enabled: false,
        state: "not_ready",
        checks: [
          {
            code: "workspace",
            state: "not_ready",
            message: "Configure an 8 GiB memory workspace on API and workers.",
            evidence: { totalBytes: 67108864 },
          },
        ],
      },
    ],
  },
};
const meta: Meta<typeof NitroTasksView> = {
  title: "Screens/Clusters/Settings/NitroTasks",
  component: NitroTasksView,
  args: props,
};
export default meta;
type Story = StoryObj<typeof NitroTasksView>;
export const Prerequisites: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Enable Isolated tasks" })).toBeEnabled();
    await expect(canvas.getByRole("button", { name: "Enable Confidential tasks" })).toBeDisabled();
  },
};
export const Loading: Story = { args: { loading: true, readiness: null } };
export const ReadFailed: Story = {
  args: { readiness: null, error: "Readiness could not be reached." },
};
export const Unsupported: Story = { args: { readiness: { ...props.readiness!, modes: [] } } };
export const ReadOnly: Story = { args: { readOnly: true } };
export const Saving: Story = { args: { saving: true } };
export const EnabledButUnreachable: Story = {
  args: {
    readiness: {
      ...props.readiness!,
      modes: [
        {
          ...props.readiness!.modes[1],
          enabled: true,
          state: "unknown",
          checks: [
            {
              code: "provider_probe",
              state: "unknown",
              message: "Provider readiness could not complete.",
              evidence: {},
            },
          ],
        },
      ],
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("button", { name: "Disable Confidential tasks" })
    ).toBeEnabled();
  },
};
export const Mobile: Story = { globals: { viewport: { value: "mobile1", isRotated: false } } };
export const LongStrings: Story = {
  args: {
    readiness: {
      ...props.readiness!,
      namespace: "organization-".repeat(12),
      modes: props.readiness!.modes.map((row) => ({
        ...row,
        checks: row.checks.map((check) => ({
          ...check,
          message: "Long prerequisite evidence ".repeat(12),
        })),
      })),
    },
  },
};
