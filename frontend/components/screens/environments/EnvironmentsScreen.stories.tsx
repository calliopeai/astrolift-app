import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import {
  ALL_ENVIRONMENTS,
  ENV_LONG,
  ENV_PROD,
  ENV_STAGING_PAUSED,
  environmentsProps,
} from "./environments.fixtures";
import { ENVIRONMENTS_LIST, selectEnvironments } from "./environments-list";
import { EnvironmentsScreen, type EnvironmentsScreenProps } from "./EnvironmentsScreen";

const meta: Meta = {
  title: "Screens/Environments/EnvironmentsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<EnvironmentsScreenProps, "list">> & {
  all?: AstroliftAppEnvironment[];
  initial?: Partial<ListState>;
};

/** The screen over fixture environments, filtered and paged the way the hook does it. */
function Environments({ all = ALL_ENVIRONMENTS, initial, ...patch }: Props) {
  const list = useLocalListState(ENVIRONMENTS_LIST, initial);
  const { rows, totalCount } = selectEnvironments(all, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return <EnvironmentsScreen {...environmentsProps({ rows, totalCount, ...patch })} list={list} />;
}

/** All: rows open the environment detail. */
export const Full: Story = { render: () => <Environments /> };

export const Loading: Story = { render: () => <Environments all={[]} loading /> };

export const Empty: Story = { render: () => <Environments all={[]} /> };

export const EmptyFiltered: Story = {
  render: () => <Environments initial={{ filters: { cluster: "no-such-cluster" } }} />,
};

export const ErrorState: Story = {
  render: () => <Environments all={[]} error={{ message: "upstream timed out" }} />,
};

export const Production: Story = {
  render: () => <Environments initial={{ view: "production" }} />,
};

export const Previews: Story = { render: () => <Environments initial={{ view: "previews" }} /> };

/** Mine: empty, with the note saying why. */
export const Mine: Story = { render: () => <Environments initial={{ view: "mine" }} /> };

/** Embedded on an agent's surface, with its tab bar above; rows stay put. */
export const Embedded: Story = {
  render: () => (
    <Environments
      appSlug="storefront"
      all={[ENV_PROD, ENV_STAGING_PAUSED]}
      tabs={
        <AppTabsView
          slug="storefront"
          basePath="/apps"
          pathname="/apps/storefront/settings"
          active="settings"
        />
      }
    />
  ),
};

/** No app.deploy permission: no pause or resume in the row menu. */
export const ReadOnly: Story = { render: () => <Environments canPause={false} /> };

/** A pause or resume in flight: the row actions are disabled. */
export const Busy: Story = { render: () => <Environments busy /> };

/** A long app, environment, cluster and an unbroken URL. */
export const LongStrings: Story = {
  render: () => <Environments all={[ENV_LONG, ...ALL_ENVIRONMENTS]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Environments all={[ENV_LONG, ...ALL_ENVIRONMENTS]} />
    </div>
  ),
};
