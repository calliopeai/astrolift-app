import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import { AppTabsView } from "../detail/AppTabs";

import { APP, PREVIEWS, PREVIEWS_EMPTY, PREVIEWS_LONG } from "./app-security-previews.fixtures";
import { AppPreviewsScreen } from "./AppPreviewsScreen";

const tabs = (
  <AppTabsView
    slug="checkout"
    basePath="/apps"
    pathname="/apps/checkout/previews"
    active="previews"
  />
);

const meta: Meta<typeof AppPreviewsScreen> = {
  title: "Screens/Apps/Security/AppPreviewsScreen",
  component: AppPreviewsScreen,
  parameters: { layout: "fullscreen" },
  args: { ...PREVIEWS, tabs },
};
export default meta;

type Story = StoryObj<typeof AppPreviewsScreen>;

/** Running, manual, failed and torn-down previews, a stale one, and the spend roll-up. */
export const Full: Story = {};

export const Loading: Story = { args: { app: null, loading: true, tabs: null } };

/** The app is loaded and the table's first page is in flight. */
export const TableLoading: Story = {
  args: {
    ...PREVIEWS_EMPTY,
    table: fakeController<AstroliftPreviewEnvironment>({ state: "loading", sortEnabled: false }),
  },
};

/** No previews yet: the empty state, then the onboarding steps. */
export const Empty: Story = { args: { ...PREVIEWS_EMPTY } };

/** Previews switched off for this app: the warning card and "Enable previews". */
export const Disabled: Story = {
  args: { ...PREVIEWS_EMPTY, app: { ...APP, previewEnabled: false } },
};

export const TableError: Story = {
  args: {
    ...PREVIEWS_EMPTY,
    table: fakeController<AstroliftPreviewEnvironment>({
      state: "error",
      error: new Error("Network error: failed to fetch"),
      sortEnabled: false,
    }),
  },
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = { args: { app: null, slug: "no-such-app", tabs: null } };

export const LongStrings: Story = { args: { ...PREVIEWS_LONG } };
