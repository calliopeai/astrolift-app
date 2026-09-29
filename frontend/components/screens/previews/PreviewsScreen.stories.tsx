import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import { LONG_PREVIEW, PREVIEWS, previewsProps } from "./previews.fixtures";
import { narrowPreviews, PREVIEWS_LIST } from "./previews-list";
import { PreviewsScreen, type PreviewsScreenProps } from "./PreviewsScreen";

const meta: Meta = {
  title: "Screens/Previews/PreviewsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<PreviewsScreenProps, "list">> & {
  all?: AstroliftPreviewEnvironment[];
  initial?: Partial<ListState>;
};

/** The screen over fixture previews, narrowed the way the hook does it. */
function Previews({ all = PREVIEWS, initial, ...patch }: Props) {
  const list = useLocalListState(PREVIEWS_LIST, initial);
  const f = list.filters;
  const served = all.filter((p) => !f.app || p.registeredAppSlug === f.app);
  return (
    <PreviewsScreen {...previewsProps({ rows: narrowPreviews(served, f), ...patch })} list={list} />
  );
}

export const Full: Story = { render: () => <Previews /> };

export const Loading: Story = { render: () => <Previews all={[]} loading /> };

export const Empty: Story = { render: () => <Previews all={[]} totalCount={0} /> };

export const EmptyFiltered: Story = {
  render: () => <Previews initial={{ filters: { app: "no-such-app" } }} />,
};

export const ErrorState: Story = {
  render: () => <Previews all={[]} error={{ message: "upstream timed out" }} />,
};

/** Mine: empty, with the note saying why. */
export const Mine: Story = { render: () => <Previews initial={{ view: "mine" }} /> };

/** The status chip: only the running previews. */
export const Running: Story = {
  render: () => <Previews initial={{ filters: { status: "running" } }} />,
};

/** Without app.deploy there is no Tear down action. */
export const ReadOnly: Story = { render: () => <Previews canTearDown={false} /> };

/** A teardown in flight: every Tear down is disabled. */
export const TearingDown: Story = { render: () => <Previews tearingDown /> };

export const LongStrings: Story = {
  render: () => <Previews all={[LONG_PREVIEW, ...PREVIEWS]} nextCursor="c:25" />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Previews all={[LONG_PREVIEW, ...PREVIEWS]} />
    </div>
  ),
};
