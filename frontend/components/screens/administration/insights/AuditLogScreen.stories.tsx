import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { AUDIT_LIST } from "./audit-list";
import { AuditLogScreen, type AuditLogScreenProps } from "./AuditLogScreen";
import { AUDIT, AUDIT_EVENTS, AUDIT_EVENTS_LONG } from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Insights/AuditLog",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<AuditLogScreenProps, "list">> & { initial?: Partial<ListState> };

/** The screen over in-memory list state, so tabs, chips and paging all click. */
function Audit({ initial, ...props }: Props) {
  const list = useLocalListState(AUDIT_LIST, initial);
  return <AuditLogScreen {...AUDIT} list={list} {...props} />;
}

export const Full: Story = { render: () => <Audit /> };

export const Loading: Story = {
  render: () => <Audit rows={[]} loading retentionDays={null} totalCount={null} />,
};

export const Empty: Story = { render: () => <Audit rows={[]} nextCursor={null} totalCount={0} /> };

/** The Denied view with nothing in it. */
export const EmptyView: Story = {
  render: () => <Audit rows={[]} nextCursor={null} initial={{ view: "denied" }} />,
};

/** The action filter matches exactly, so a partial action finds nothing. */
export const EmptyFiltered: Story = {
  render: () => (
    <Audit
      rows={[]}
      nextCursor={null}
      initial={{ q: "team", filters: { decision: "DENY", since: "7d" } }}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <Audit
      rows={[]}
      error={{ message: "upstream timed out after 30s (astroliftAuditEventsPage)" }}
    />
  ),
};

/** Mine, filtered by chips, on an older page. */
export const Filtered: Story = {
  render: () => (
    <Audit
      rows={AUDIT_EVENTS.slice(0, 2)}
      totalCount={2}
      initial={{
        view: "mine",
        filters: { action: "app.env.update", since: "24h" },
        after: "cursor-1",
      }}
    />
  ),
};

/** Target kind has no query argument yet; the page says it narrows the page only. */
export const TargetKindLocal: Story = {
  render: () => (
    <Audit
      rows={AUDIT_EVENTS.filter((e) => e.targetKind === "app")}
      totalCount={null}
      targetFilteredLocally
      initial={{ filters: { target: "app" } }}
    />
  ),
};

/** Polled events wait behind the pill; the rows being read hold still. */
export const NewEvents: Story = {
  render: () => <Audit newRows={{ count: 3, onReveal: () => {} }} />,
};

export const Exporting: Story = { render: () => <Audit exporting /> };

/** A 64-char SHA, a 200-char ARN and an unbroken URL never widen the page. */
export const LongStrings: Story = {
  render: () => (
    <Audit
      rows={AUDIT_EVENTS_LONG}
      totalCount={AUDIT_EVENTS_LONG.length}
      initial={{ filters: { actor: AUDIT_EVENTS_LONG[1].actorId } }}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6): the table scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Audit rows={[...AUDIT_EVENTS, ...AUDIT_EVENTS_LONG]} />
    </div>
  ),
};

/** A row opens the detail sheet with the before/after diff. */
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

/** Export lives in the list's overflow menu. */
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
