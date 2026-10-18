import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import type { BrainSummary } from "./brain-status-model";
import {
  BEHIND,
  DEPLOY_FAILED,
  DEPLOYING,
  HEALTHY,
  NOT_DEPLOYED,
  summaryOf,
  UNKNOWN_VALUES,
} from "./brain-status.fixtures";
import { BrainStatusCell } from "./BrainStatusCell";

const meta: Meta<typeof BrainStatusCell> = {
  title: "Screens/Brains/BrainStatusCell",
  component: BrainStatusCell,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof BrainStatusCell>;

/** The three columns side by side for one row's summary. */
function Row({ label, summary }: { label: string; summary: BrainSummary | null }) {
  return (
    <tr className="border-b align-top">
      <th scope="row" className="text-muted-foreground py-2 pr-6 text-left text-xs font-normal">
        {label}
      </th>
      {(["health", "rebuilt", "freshness"] as const).map((part) => (
        <td key={part} className="py-2 pr-6">
          <BrainStatusCell part={part} summary={summary} />
        </td>
      ))}
    </tr>
  );
}

/** Every state a row's status columns can be in, from its cached summary (#2444). */
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
        <Row label="Healthy" summary={summaryOf(HEALTHY)} />
        <Row label="Behind, ingest failed" summary={summaryOf(BEHIND)} />
        <Row label="Deploy failed" summary={summaryOf(DEPLOY_FAILED)} />
        <Row label="Deploying" summary={summaryOf(DEPLOYING)} />
        <Row label="Not deployed" summary={summaryOf(NOT_DEPLOYED)} />
        <Row label="Not checked yet" summary={summaryOf(HEALTHY, false)} />
        <Row label="No environment" summary={null} />
        <Row label="Unknown values" summary={summaryOf(UNKNOWN_VALUES)} />
      </tbody>
    </table>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Behind head")).toBeInTheDocument();
    await expect(canvas.getByText("Ingest failed")).toBeInTheDocument();
    await expect(canvas.getByText("No production environment")).toBeInTheDocument();
    await expect(canvas.getByText("paused_by_operator")).toBeInTheDocument();
    await expect(
      canvas.getByTitle("Not read from the source host yet; open the layer to check.")
    ).toBeInTheDocument();
  },
};

export const Health: Story = {
  args: { part: "health", summary: summaryOf(HEALTHY) },
};
export const Rebuilt: Story = {
  args: { part: "rebuilt", summary: summaryOf(HEALTHY) },
};
export const Freshness: Story = {
  args: { part: "freshness", summary: summaryOf(BEHIND) },
};
export const NotChecked: Story = {
  args: { part: "freshness", summary: summaryOf(HEALTHY, false) },
};
