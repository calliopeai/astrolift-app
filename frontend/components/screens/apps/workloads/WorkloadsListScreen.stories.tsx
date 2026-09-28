import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";
import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { LIST, LIST_EMPTY, LIST_LONG, SCALE } from "./app-workloads.fixtures";
import { ScalePopoverView } from "./ScalePopover";
import { WorkloadsListScreen } from "./WorkloadsListScreen";

const meta: Meta<typeof WorkloadsListScreen> = {
  title: "Screens/Apps/Workloads/WorkloadsListScreen",
  component: WorkloadsListScreen,
  parameters: { layout: "fullscreen" },
  args: {
    ...LIST,
    tabs: (
      <AppTabsView
        slug="billing"
        basePath="/apps"
        pathname="/apps/billing/workloads"
        active="workloads"
      />
    ),
    renderScale: (w, currentDesired) => (
      <ScalePopoverView {...SCALE} workloadName={w.name} currentDesired={currentDesired} />
    ),
  },
};
export default meta;

type Story = StoryObj<typeof WorkloadsListScreen>;

/** Mixed kinds: a healthy public API, an HPA worker, a crash-looping StatefulSet, a CronJob. */
export const Full: Story = {};

/** The app query has not answered yet. */
export const Loading: Story = { args: { app: null, appLoading: true } };

/** The app has no workloads in its manifest. */
export const Empty: Story = { args: LIST_EMPTY };

/** The workload page query failed; the table shows its error state. */
export const LoadFailed: Story = {
  args: {
    table: fakeController<AstroliftWorkload>({
      state: "error",
      error: new Error("Network error: Failed to fetch"),
      sortEnabled: false,
    }),
  },
};

/** No app with this slug, or no permission to read it. */
export const NotFound: Story = { args: { app: null, appLoading: false, slug: "no-such-app" } };

export const LongStrings: Story = { args: LIST_LONG };
