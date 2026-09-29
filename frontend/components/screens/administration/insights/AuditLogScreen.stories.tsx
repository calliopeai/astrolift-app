import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { AUDIT_LIST } from "./audit-list";
import { type AuditEventsFeed, AuditLogScreen, type AuditLogScreenProps } from "./AuditLogScreen";
import { AUDIT, AUDIT_EVENTS, AUDIT_EVENTS_LONG, AUDIT_FEED } from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Insights/AuditLog",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<AuditLogScreenProps, "list" | "events">> & {
  initial?: Partial<ListState>;
  feed?: Partial<AuditEventsFeed>;
};

/** The screen over in-memory list state, so tabs and chips all click. */
function Audit({ initial, feed, ...props }: Props) {
  const list = useLocalListState(AUDIT_LIST, initial);
  return <AuditLogScreen {...AUDIT} list={list} events={{ ...AUDIT_FEED, ...feed }} {...props} />;
}

export const Full: Story = { render: () => <Audit /> };

export const Loading: Story = {
  render: () => (
    <Audit feed={{ items: [], loading: true }} retentionDays={null} totalCount={null} />
  ),
};

export const Empty: Story = {
  render: () => <Audit feed={{ items: [], hasMore: false }} totalCount={0} />,
};

/** The Denied view with nothing in it. */
export const EmptyView: Story = {
  render: () => <Audit feed={{ items: [], hasMore: false }} initial={{ view: "denied" }} />,
};

/** The action filter matches exactly, so a partial action finds nothing. */
export const EmptyFiltered: Story = {
  render: () => (
    <Audit
      feed={{ items: [], hasMore: false }}
      initial={{ q: "team", filters: { decision: "DENY", since: "7d" } }}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <Audit
      feed={{
        items: [],
        error: "upstream timed out after 30s (astroliftAuditEventsPage)",
      }}
    />
  ),
};

/** An older page failed: the events stay, Load older retries. */
export const OlderPageFailed: Story = {
  render: () => <Audit feed={{ error: "upstream timed out" }} />,
};

/** Mine, filtered by chips. */
export const Filtered: Story = {
  render: () => (
    <Audit
      feed={{ items: AUDIT_EVENTS.slice(0, 2), hasMore: false }}
      totalCount={2}
      initial={{ view: "mine", filters: { action: "app.env.update", since: "24h" } }}
    />
  ),
};

/** Target kind has no query argument yet; the page says it narrows the page only. */
export const TargetKindLocal: Story = {
  render: () => (
    <Audit
      feed={{ items: AUDIT_EVENTS.filter((e) => e.targetKind === "app") }}
      totalCount={null}
      targetFilteredLocally
      initial={{ filters: { target: "app" } }}
    />
  ),
};

/** Polled events wait behind the pill; the events being read hold still. */
export const NewEvents: Story = { render: () => <Audit feed={{ newCount: 3 }} /> };

export const Exporting: Story = { render: () => <Audit exporting /> };

/** A 64-char SHA, a 200-char ARN and an unbroken URL never widen the page. */
export const LongStrings: Story = {
  render: () => (
    <Audit
      feed={{ items: AUDIT_EVENTS_LONG }}
      totalCount={AUDIT_EVENTS_LONG.length}
      initial={{ filters: { actor: AUDIT_EVENTS_LONG[1].actorId } }}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6): the feed scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Audit feed={{ items: [...AUDIT_EVENTS, ...AUDIT_EVENTS_LONG] }} />
    </div>
  ),
};

/** A line opens the detail sheet with the before/after diff. */
export const RowDetails: Story = {
  render: () => <Audit />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getAllByRole("button", { name: /^app\.env\.update/ })[0]);
    await expect(await within(document.body).findByRole("dialog")).toBeInTheDocument();
  },
};

/** The retention editor, seeded from the current window. */
export const RetentionEditor: Story = {
  render: () => <Audit />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Retention/ }));
    const dialog = within(await within(document.body).findByRole("dialog"));
    await expect(dialog.getByLabelText(/Keep audit events for/)).toHaveValue(365);
  },
};

/** Export lives in the filter bar's overflow menu. */
export const ExportMenu: Story = {
  render: () => <Audit />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Export" }));
    await expect(
      await within(document.body).findByRole("menuitem", { name: /Export as CSV/ })
    ).toBeInTheDocument();
  },
};
