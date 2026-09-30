import type { Decorator, Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider, useTranslations } from "next-intl";
import spanish from "@/messages/es.json";

import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { APPS_LIST, type AppRow, localizedAppsList, pinFirst } from "./apps-list";
import { AppsListScreen, type AppsListScreenProps, PushSecretsDialog } from "./AppsListScreen";
import { APPS, LONG_APP, MANY, PUSH_SECRETS, listProps } from "./fixtures";

// What each view's page holds, as the server answers it.
const ACTIVE = APPS.filter((a) => !a.isArchived);
const FAILING = ACTIVE.filter(
  (a) => a.provisioningStatus === "failed" || a.healthPulse?.status === "DEGRADED"
);

// List or cards is a per-person preference in localStorage; pin it per story
// so one story's mode never leaks into the next.
const withMode: Decorator = (Story, { parameters }) => {
  try {
    localStorage.setItem(
      `astrolift.list.${APPS_LIST.id}.mode`,
      JSON.stringify(parameters.mode ?? "list")
    );
  } catch {
    // storage unavailable: the list view
  }
  return <Story />;
};

const meta: Meta = {
  title: "Screens/Apps/List/AppsListScreen",
  parameters: { layout: "padded" },
  decorators: [withMode],
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<AppsListScreenProps, "list">> & {
  apps?: AppRow[];
  initial?: Partial<ListState>;
};

/**
 * The screen over fixture apps. The server filters, sorts and pages, so a
 * story passes the rows its page would return; this only slices them into
 * numbered pages and lifts the pins, as the hook does with a page.
 */
function Apps({ apps = ACTIVE, initial, ...patch }: Props) {
  const t = useTranslations("apps.list");
  const status = useTranslations("apps.frame");
  const list = useLocalListState(localizedAppsList(t, status), initial);
  const pinned = patch.pinned ?? new Set(["billing-worker"]);
  const { page, pageSize } = list.state;
  const rows = pinFirst(apps.slice((page - 1) * pageSize, page * pageSize), pinned);
  return (
    <AppsListScreen
      {...listProps({ rows, totalCount: apps.length, pinned, ...patch })}
      list={list}
    />
  );
}

/** Newest first, one app pinned to the top of the page. */
export const Full: Story = { render: () => <Apps /> };

export const Loading: Story = { render: () => <Apps apps={[]} loading /> };

/** No apps registered yet: the create action and Get started. */
export const Empty: Story = { render: () => <Apps apps={[]} /> };

/** A chip that matches nothing: "No apps match" and Clear. */
export const EmptyFiltered: Story = {
  render: () => <Apps apps={[]} initial={{ filters: { cluster: "gke-eu-west-4" } }} />,
};

/** The Failing view with nothing failing. */
export const EmptyView: Story = {
  render: () => <Apps apps={[]} initial={{ view: "failing" }} />,
};

export const ErrorState: Story = {
  render: () => <Apps apps={[]} error={{ message: "Network error: failed to fetch" }} />,
};

/** Rows answer the previous search while the next loads. */
export const Refetching: Story = { render: () => <Apps stale /> };

/** Mine: the stand-in note says what it covers. */
export const Mine: Story = { render: () => <Apps initial={{ view: "mine" }} /> };

/** Failing: provisioning failed, or the latest deploy did. */
export const Failing: Story = {
  render: () => <Apps apps={FAILING} initial={{ view: "failing" }} />,
};

export const Archived: Story = {
  render: () => <Apps apps={APPS.filter((a) => a.isArchived)} initial={{ view: "archived" }} />,
};

/** Project, kind and cluster chips. */
export const Filtered: Story = {
  render: () => (
    <Apps
      apps={ACTIVE.filter(
        (a) =>
          a.projectSlug === "core" &&
          a.topology === "service-worker" &&
          a.clusters.includes("prd-us-west-2")
      )}
      initial={{ filters: { project: "core", kind: "service-worker", cluster: "prd-us-west-2" } }}
    />
  ),
};

/** Sixty apps: numbered pages, page 2 of 3. */
export const Paged: Story = { render: () => <Apps apps={MANY} initial={{ page: 2 }} /> };

/** A viewer without app.deploy: no selection, no bulk bar. */
export const ReadOnly: Story = { render: () => <Apps canDeploy={false} /> };

export const Cards: Story = { parameters: { mode: "card" }, render: () => <Apps /> };

export const CardsLoading: Story = {
  parameters: { mode: "card" },
  render: () => <Apps apps={[]} loading />,
};

export const CardsEmpty: Story = { parameters: { mode: "card" }, render: () => <Apps apps={[]} /> };

export const CardsError: Story = {
  parameters: { mode: "card" },
  render: () => <Apps apps={[]} error={{ message: "upstream timed out" }} />,
};

/** A 64-char SHA image tag, a 200-char ARN description and an unbroken repo URL. */
export const LongStrings: Story = { render: () => <Apps apps={[LONG_APP, ...ACTIVE]} /> };

export const LongStringsCards: Story = {
  parameters: { mode: "card" },
  render: () => <Apps apps={[LONG_APP, ...ACTIVE]} />,
};

/** The narrowest supported width: the table scrolls in its own frame, the page does not. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Apps apps={[LONG_APP, ...MANY]} />
    </div>
  ),
};

export const Width768Cards: Story = {
  parameters: { mode: "card" },
  render: () => (
    <div style={{ width: 768 }}>
      <Apps apps={[LONG_APP, ...ACTIVE]} />
    </div>
  ),
};

/** Views are the header's tabs, New app links to its page, and Apps ▾ switches functions. */
export const HeaderNavigation: Story = {
  render: () => <Apps />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const view of ["All", "Mine", "Failing", "Archived"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
    await expect(canvas.getByRole("link", { name: "New app" })).toHaveAttribute(
      "href",
      "/apps/new"
    );
    await userEvent.click(canvas.getByRole("button", { name: "Apps: switch" }));
    await expect(
      await within(document.body).findByRole("menuitem", { name: /Deployments/ })
    ).toBeInTheDocument();
  },
};

export const PushSecrets: Story = { render: () => <PushSecretsDialog {...PUSH_SECRETS} /> };

export const PushSecretsBusy: Story = {
  render: () => <PushSecretsDialog {...PUSH_SECRETS} busy />,
};

/** Translated app kinds, filter/view labels and registry columns at minimum console width. */
export const SpanishWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="es" messages={spanish}>
      <div style={{ width: 768 }}>
        <Apps />
      </div>
    </NextIntlClientProvider>
  ),
};
