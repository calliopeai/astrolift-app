import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { PipelineDag, type PipelineDagStage } from "@/components/viz/pipeline-dag";

const meta: Meta = { title: "Patterns/Viz/PipelineDag", parameters: { layout: "padded" } };
export default meta;

const STAGES: PipelineDagStage[] = [
  { id: "fetch", name: "fetch", status: "succeeded" },
  { id: "transform", name: "transform", status: "succeeded", needs: ["fetch"] },
  { id: "validate", name: "validate", status: "running", needs: ["transform"] },
  { id: "publish", name: "publish", status: "pending", needs: ["validate"] },
  { id: "notify", name: "notify", status: "pending", needs: ["validate"] },
];

export const Running: StoryObj = {
  render: () => <PipelineDag stages={STAGES} height={320} ariaLabel="nightly-sync run" />,
};
export const Failed: StoryObj = {
  render: () => (
    <PipelineDag
      stages={STAGES.map((s) => (s.id === "validate" ? { ...s, status: "failed" } : s))}
      height={320}
      ariaLabel="nightly-sync run, failed"
    />
  ),
};
