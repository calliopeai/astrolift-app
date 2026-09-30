import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  LIST,
  LIST_EMPTY,
  LIST_LONG,
  SCALE,
  WORKLOADS,
  type WorkloadsListFixture,
} from "./app-workloads.fixtures";
import { ScalePopoverView } from "./ScalePopover";
import { WorkloadRowActions } from "./WorkloadRowActions";
import { APP_WORKLOADS_LIST } from "./workloads-list";
import { WorkloadsListScreen } from "./WorkloadsListScreen";

/** An app's Workloads tab: the embedded list with a kind chip (spec 44 §5.1, §10.2). */
const meta: Meta = {
  title: "Screens/Apps/Workloads/WorkloadsListScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const ENVS = [
  { id: "env-1", name: "production" },
  { id: "env-2", name: "staging" },
];

function Workloads({
  initial,
  ...props
}: Partial<WorkloadsListFixture> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(APP_WORKLOADS_LIST, initial);
  return (
    <WorkloadsListScreen
      {...LIST}
      list={list}
      renderScale={(w, currentDesired) => (
        <ScalePopoverView
          {...SCALE}
          permission={w.viewerCan.scale}
          workloadName={w.name}
          currentDesired={currentDesired}
        />
      )}
      renderRowActions={(w) => (
        <WorkloadRowActions
          workload={w}
          logsHref={`#logs-${w.slug}`}
          environments={ENVS}
          pendingSlug={null}
          canRun
          onRun={() => {}}
        />
      )}
      {...props}
    />
  );
}

/** Mixed kinds: a healthy public API, an HPA worker, a crash-looping StatefulSet, a CronJob. */
export const Full: Story = { render: () => <Workloads /> };

/** Scheduled jobs: the same list, filtered to the cronjob kind (`/jobs` lands here). */
export const ScheduledJobs: Story = {
  render: () => (
    <Workloads
      rows={WORKLOADS.filter((w) => w.kind === "cronjob")}
      totalCount={1}
      initial={{ filters: { kind: "cronjob" } }}
    />
  ),
};

export const Loading: Story = { render: () => <Workloads rows={[]} loading /> };

/** The app has no workloads in its manifest. */
export const Empty: Story = { render: () => <Workloads {...LIST_EMPTY} /> };

/** A kind with none of that kind. */
export const EmptyFiltered: Story = {
  render: () => (
    <Workloads {...LIST_EMPTY} initial={{ filters: { kind: "statefulset", exposure: "public" } }} />
  ),
};

/** The workloads query failed; the list shows its error in its own frame. */
export const LoadFailed: Story = {
  render: () => <Workloads {...LIST_EMPTY} error={{ message: "Network error: Failed to fetch" }} />,
};

export const LongStrings: Story = { render: () => <Workloads {...LIST_LONG} /> };

/** The narrowest the web console goes (spec 44 §6): the table scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Workloads {...LIST_LONG} />
    </div>
  ),
};

export const RestrictedRow: Story = {
  render: () => (
    <Workloads
      rows={[
        {
          ...WORKLOADS[0],
          viewerCan: {
            restart: { allowed: false, code: "PERMISSION_DENIED", reason: "No app grant" },
            scale: {
              allowed: false,
              code: "PERMISSION_DENIED",
              reason: "Sibling app is outside the grant",
            },
          },
        },
        WORKLOADS[2],
      ]}
      totalCount={2}
    />
  ),
};
