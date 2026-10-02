import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { ProjectManagedResourceList } from "./ProjectManagedResourceList";
import { RESOURCE } from "./resource-reads.fixtures";

const meta: Meta<typeof ProjectManagedResourceList> = {
  title: "Screens/Projects/ManagedResourceList",
  component: ProjectManagedResourceList,
  args: {
    controller: fakeController({ rows: [RESOURCE], totalCount: 251, hasNext: true }),
    onOpen: () => {},
  },
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Paged: Story = {};
export const Empty: Story = {
  args: { controller: fakeController({ rows: [], state: "empty", totalCount: 0 }) },
};
export const FilteredEmpty: Story = {
  args: {
    controller: fakeController({
      rows: [],
      state: "emptyFiltered",
      isFiltered: true,
      search: "missing",
    }),
  },
};
export const RefusedContinuation: Story = {
  args: {
    controller: fakeController({
      rows: [],
      state: "error",
      error: new Error("Restart the resource page"),
      hasPrev: true,
      pageIndex: 1,
    }),
  },
};
export const Loading: Story = {
  args: { controller: fakeController({ rows: [], state: "loading" }) },
};
export const UnknownStatus: Story = {
  args: { controller: fakeController({ rows: [{ ...RESOURCE, status: "provider_phase_v2" }] }) },
};

export const LongStrings: Story = {
  args: {
    controller: fakeController({
      rows: [
        {
          ...RESOURCE,
          name: "shared-resource-".repeat(24),
          clusterSlug: "cluster-".repeat(24),
          variant: "provider-variant-".repeat(24),
        },
      ],
      totalCount: 251,
      hasNext: true,
    }),
  },
};
export const Width768: Story = {};
