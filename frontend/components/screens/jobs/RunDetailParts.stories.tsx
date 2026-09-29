import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { HistoryIcon } from "lucide-react";

import { appsDetailCrumbs } from "@/components/screens/deployments/apps-area";

import { RunMenu, RunMissing } from "./RunDetailParts";

const meta: Meta = { title: "Screens/Jobs/RunDetailParts", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

const CRUMBS = appsDetailCrumbs("jobs", { label: "run 4e1b0d8f" });
const EMPTY = {
  icon: <HistoryIcon className="size-5" />,
  title: "Job run not found",
  description: "No run has this id, or you cannot see it.",
  actionHref: "/jobs/runs",
  actionLabel: "Open job runs",
};

export const Menu: Story = {
  render: () => (
    <RunMenu
      id="4e1b0d8f-7c3d-4a69-8920-1b6d1a5c0e02"
      links={[
        { label: "Open app", href: "/apps/billing" },
        { label: "Open workload", href: "/apps/billing/workloads/nightly-report" },
      ]}
    />
  ),
};

export const NotFound: Story = {
  render: () => (
    <RunMissing
      crumbs={CRUMBS}
      title="Job run"
      icon={<HistoryIcon className="size-4" />}
      error={null}
      onRetry={() => {}}
      empty={EMPTY}
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <RunMissing
      crumbs={CRUMBS}
      title="Job run"
      icon={<HistoryIcon className="size-4" />}
      error={{ message: "Network error: failed to fetch" }}
      onRetry={() => {}}
      empty={EMPTY}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <RunMissing
      crumbs={appsDetailCrumbs("jobs", { label: `run ${"a".repeat(64)}` })}
      title={`Job run ${"x".repeat(120)}`}
      icon={<HistoryIcon className="size-4" />}
      error={{ message: `https://registry.example.com/${"segment".repeat(40)}` }}
      onRetry={() => {}}
      empty={EMPTY}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <RunMissing
        crumbs={CRUMBS}
        title="Job run"
        icon={<HistoryIcon className="size-4" />}
        error={null}
        onRetry={() => {}}
        empty={EMPTY}
      />
    </div>
  ),
};
