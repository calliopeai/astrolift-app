import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import es from "@/messages/es.json";
import ja from "@/messages/ja.json";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { APP_PREVIEWS_LIST } from "../deployments/app-deployments-list";
import {
  APP,
  PREVIEWS,
  PREVIEWS_EMPTY,
  PREVIEWS_LONG,
  type PreviewsFixture,
} from "./app-security-previews.fixtures";
import { AppPreviewsScreen } from "./AppPreviewsScreen";

/** The Previews view of an app's Deployments tab (spec 44 §4.4): preview environments. */
const meta: Meta = {
  title: "Screens/Apps/Security/AppPreviewsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Previews({
  initial,
  ...props
}: Partial<PreviewsFixture> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(APP_PREVIEWS_LIST, { view: "previews", ...initial });
  return <AppPreviewsScreen {...PREVIEWS} list={list} {...props} />;
}

/** Running, manual, failed and torn-down previews, a stale one, and the spend roll-up. */
export const Full: Story = { render: () => <Previews /> };

/** The app is still loading: the list holds its skeleton. */
export const Loading: Story = { render: () => <Previews app={null} rows={[]} /> };

/** The app is loaded and the list's first page is in flight. */
export const TableLoading: Story = {
  render: () => <Previews {...PREVIEWS_EMPTY} pageLoading />,
};

/** No previews yet: the empty state, then the onboarding steps. */
export const Empty: Story = { render: () => <Previews {...PREVIEWS_EMPTY} /> };

/** Previews switched off for this app: the warning strip and "Enable previews". */
export const Disabled: Story = {
  render: () => <Previews {...PREVIEWS_EMPTY} app={{ ...APP, previewEnabled: false }} />,
};

export const TableError: Story = {
  render: () => (
    <Previews {...PREVIEWS_EMPTY} pageError={{ message: "Network error: failed to fetch" }} />
  ),
};

/** Without app.deploy: no row menu and no Create preview. */
export const ReadOnly: Story = { render: () => <Previews canDeploy={false} /> };

export const LongStrings: Story = { render: () => <Previews {...PREVIEWS_LONG} /> };

/** The narrowest the web console goes (spec 44 §6): the table scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Previews {...PREVIEWS_LONG} rows={[...PREVIEWS_LONG.rows, ...PREVIEWS.rows]} />
    </div>
  ),
};

export const Spanish: Story = {
  render: () => <Previews />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="es" messages={es} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Japanese: Story = {
  render: () => <Previews />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
