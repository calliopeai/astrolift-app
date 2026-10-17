import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { selectRows } from "@/components/list/select-rows";
import { useLocalListState } from "@/components/list/use-list-state";

import { HOST_PROJECTS } from "../brains.fixtures";
import { BRAIN_APPS_LIST, BRAIN_APPS_SELECT } from "./brain-detail-lists";
import { hostedAppRows } from "./brain-detail-model";
import { BRAIN } from "./brain-detail.fixtures";
import { BrainAppsTab, type BrainAppsTabProps } from "./BrainAppsTab";

const meta: Meta = {
  title: "Screens/Brains/Detail/BrainAppsTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const onHost = fn();

function Apps({
  hosted = BRAIN.hostedApps ?? [],
  ...patch
}: Partial<BrainAppsTabProps> & { hosted?: { slug: string; environment: string }[] }) {
  const list = useLocalListState(BRAIN_APPS_LIST);
  const page = selectRows(
    hostedAppRows(hosted),
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    BRAIN_APPS_SELECT
  );
  return (
    <BrainAppsTab
      slug={BRAIN.slug}
      served
      canHost
      list={list}
      rows={page.rows}
      totalCount={page.totalCount}
      hosting={false}
      onHost={onHost}
      onDismissHost={() => {}}
      hostProjects={HOST_PROJECTS}
      hostSubmitting={false}
      onSubmitHost={() => {}}
      {...patch}
    />
  );
}

/** One row per app, its environments together; a row opens the app. */
export const Hosted: Story = {
  render: () => <Apps />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const row = within(canvas.getByRole("row", { name: /brain-chat/ }));
    await expect(row.getByText("staging")).toBeInTheDocument();
    await expect(row.getAllByRole("link")[0]).toHaveAttribute("href", "/apps/brain-chat");
    await userEvent.click(canvas.getByRole("button", { name: "Host an app" }));
    await expect(onHost).toHaveBeenCalled();
  },
};

/** The template dialog, open. */
export const Hosting: Story = {
  render: () => <Apps hosting />,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Host an app on platform-brain")).toBeInTheDocument();
  },
};

export const Empty: Story = { render: () => <Apps hosted={[]} /> };

/** Not served yet: nothing to host on. */
export const NotServed: Story = {
  render: () => <Apps hosted={[]} served={false} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("button", { name: "Host an app" })).toBeDisabled();
  },
};

/** Without app.create: the list only. */
export const ReadOnly: Story = {
  render: () => <Apps canHost={false} />,
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole("button", { name: "Host an app" })
    ).not.toBeInTheDocument();
  },
};
