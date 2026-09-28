import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { RunStatusBadge } from "@/components/jobs/RunStatusBadge";
import { StatusDot } from "@/components/StatusDot";

/**
 * The one status system (spec 44 §7): dot, pill and run badge share a tone
 * map built from the status tokens, so switching the accent in the toolbar
 * must never change a status colour.
 */
const meta: Meta = { title: "Primitives/Status" };
export default meta;

export const Dots: StoryObj = {
  render: () => (
    <div className="flex items-center gap-4 text-sm">
      {(["ok", "warn", "error", "pending", "muted"] as const).map((s) => (
        <span key={s} className="inline-flex items-center gap-2">
          <StatusDot status={s} /> {s}
        </span>
      ))}
    </div>
  ),
};

export const DeploymentPills: StoryObj = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      {(
        [
          "pending_approval",
          "pending",
          "deploying",
          "redeploying",
          "running",
          "failed",
          "rolled_back",
          "superseded",
        ] as const
      ).map((s) => (
        <DeploymentStatusPill key={s} status={s} />
      ))}
    </div>
  ),
};

export const RunBadges: StoryObj = {
  render: () => (
    <div className="flex flex-col gap-2">
      <RunStatusBadge status="running" />
      <RunStatusBadge status="succeeded" exitCode={0} />
      <RunStatusBadge status="failed" exitCode={137} />
      <RunStatusBadge status="superseded" />
    </div>
  ),
};
