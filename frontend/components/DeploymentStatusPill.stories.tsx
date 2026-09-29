import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";

const meta: Meta = { title: "Primitives/Status/DeploymentStatusPill" };
export default meta;

export const All: StoryObj = {
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
