import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { GateState } from "../core/workflow-model";
import { VizLegend } from "../core/VizLegend";

import { AIRLOCK_LEGEND, AirlockGate } from "./AirlockGate";

const NOW = Date.UTC(2026, 8, 28, 14, 0, 0);

function Demo({
  state = "waiting",
  heldMs = 252_000,
  decidedBy,
  actions = false,
  motion = "full",
}: {
  state?: GateState;
  heldMs?: number;
  decidedBy?: string;
  actions?: boolean;
  motion?: "full" | "reduced";
}) {
  return (
    <div data-motion={motion} className="bg-card inline-flex flex-col gap-3 rounded-md border p-4">
      <AirlockGate
        state={state}
        waitingSince={NOW - heldMs}
        now={NOW}
        decidedBy={decidedBy}
        motion={motion}
        onApprove={actions ? () => {} : undefined}
        onDeny={actions ? () => {} : undefined}
      />
      <VizLegend items={AIRLOCK_LEGEND} motion={motion} className="max-w-sm" />
    </div>
  );
}

/** A live gate: a run arrives and waits; the reviewer decides; the next run arrives. */
function LiveGate() {
  const [state, setState] = React.useState<GateState>("waiting");
  const [since, setSince] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (state === "waiting") return;
    const id = window.setTimeout(() => {
      setState("waiting");
      setSince(Date.now());
    }, 4000);
    return () => window.clearTimeout(id);
  }, [state]);
  return (
    <div className="bg-card inline-flex flex-col gap-3 rounded-md border p-4">
      <AirlockGate
        state={state}
        waitingSince={since}
        decidedBy={state === "waiting" ? undefined : "reviewer@example.com"}
        onApprove={() => setState("approved")}
        onDeny={() => setState("denied")}
      />
      <VizLegend items={AIRLOCK_LEGEND} motion="full" className="max-w-sm" />
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/Workflow/AirlockGate",
  component: Demo,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { render: () => <LiveGate /> };
export const Waiting: Story = { args: { state: "waiting", actions: true } };
export const Quiet: Story = { args: { state: "approved", decidedBy: "leo@example.com" } };
export const Incident: Story = { args: { state: "denied", decidedBy: "security@example.com" } };
export const Large: Story = { args: { state: "waiting", heldMs: 3 * 3_600_000 + 17 * 60_000 } };
export const Reduced: Story = { args: { state: "waiting", actions: true, motion: "reduced" } };
export const LongStrings: Story = {
  args: {
    state: "approved",
    decidedBy: "a.very.long.reviewer.name@enterprise-customer-subsidiary.example.com",
  },
};
