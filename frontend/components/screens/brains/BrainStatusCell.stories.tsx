import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import type { BrainStatus } from "./brain-status-model";
import {
  BEHIND,
  DEPLOY_FAILED,
  DEPLOYING,
  HEALTHY,
  NOT_DEPLOYED,
  UNKNOWN_VALUES,
} from "./brain-status.fixtures";
import { type BrainStatusResult, BrainStatusCell } from "./BrainStatusCell";

const meta: Meta<typeof BrainStatusCell> = {
  title: "Screens/Brains/BrainStatusCell",
  component: BrainStatusCell,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof BrainStatusCell>;

const ready = (status: BrainStatus | null): BrainStatusResult => ({ state: "ready", status });

/** The three columns side by side for one read. */
function Row({ label, result }: { label: string; result: BrainStatusResult }) {
  return (
    <tr className="border-b align-top">
      <th scope="row" className="text-muted-foreground py-2 pr-6 text-left text-xs font-normal">
        {label}
      </th>
      {(["health", "rebuilt", "freshness"] as const).map((part) => (
        <td key={part} className="py-2 pr-6">
          <BrainStatusCell part={part} result={result} environment="production" />
        </td>
      ))}
    </tr>
  );
}

/** Every state a row's status columns can be in. */
export const AllStates: Story = {
  render: () => (
    <table className="text-sm">
      <thead>
        <tr className="text-muted-foreground text-left text-xs">
          <th className="pr-6">State</th>
          <th className="pr-6">Health</th>
          <th className="pr-6">Last rebuild</th>
          <th className="pr-6">Freshness</th>
        </tr>
      </thead>
      <tbody>
        <Row label="Healthy" result={ready(HEALTHY)} />
        <Row label="Behind, ingest failed" result={ready(BEHIND)} />
        <Row label="Deploy failed" result={ready(DEPLOY_FAILED)} />
        <Row label="Deploying" result={ready(DEPLOYING)} />
        <Row label="Not deployed" result={ready(NOT_DEPLOYED)} />
        <Row label="No such environment" result={ready(null)} />
        <Row label="Unknown values" result={ready(UNKNOWN_VALUES)} />
        <Row label="Loading" result={{ state: "loading" }} />
        <Row label="Failed" result={{ state: "error", message: "source head: HTTP 502" }} />
      </tbody>
    </table>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Behind head")).toBeInTheDocument();
    await expect(canvas.getByText("Ingest failed")).toBeInTheDocument();
    await expect(canvas.getByText("No production environment")).toBeInTheDocument();
    await expect(canvas.getByText("paused_by_operator")).toBeInTheDocument();
    await expect(canvas.getByTitle("source head: HTTP 502")).toBeInTheDocument();
  },
};

export const Health: Story = {
  args: { part: "health", result: ready(HEALTHY), environment: "production" },
};
export const Rebuilt: Story = {
  args: { part: "rebuilt", result: ready(HEALTHY), environment: "production" },
};
export const Freshness: Story = {
  args: { part: "freshness", result: ready(BEHIND), environment: "production" },
};
export const Loading: Story = {
  args: { part: "health", result: { state: "loading" }, environment: "production" },
};
