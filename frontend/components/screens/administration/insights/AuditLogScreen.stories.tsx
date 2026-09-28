import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftAuditEvent } from "@/graphql/operations/operations.types";

import { AuditLogScreen } from "./AuditLogScreen";
import { AUDIT, AUDIT_EVENTS_LONG } from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Insights/AuditLog",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <AuditLogScreen {...AUDIT} /> };

export const Loading: Story = {
  render: () => (
    <AuditLogScreen
      {...AUDIT}
      retentionDays={null}
      table={fakeController<AstroliftAuditEvent>({ state: "loading", sort: undefined })}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <AuditLogScreen
      {...AUDIT}
      table={fakeController<AstroliftAuditEvent>({ state: "empty", sort: undefined })}
    />
  ),
};

/** The action filter matches exactly, so a partial action finds nothing. */
export const EmptyFiltered: Story = {
  render: () => (
    <AuditLogScreen
      {...AUDIT}
      decisionFilter="DENY"
      fromDate="2026-09-01"
      table={fakeController<AstroliftAuditEvent>({
        state: "emptyFiltered",
        isFiltered: true,
        search: "team",
        sort: undefined,
      })}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <AuditLogScreen
      {...AUDIT}
      table={fakeController<AstroliftAuditEvent>({
        state: "error",
        error: new globalThis.Error("upstream timed out"),
        sort: undefined,
      })}
    />
  ),
};

export const Exporting: Story = { render: () => <AuditLogScreen {...AUDIT} exporting /> };

export const LongStrings: Story = {
  render: () => (
    <AuditLogScreen
      {...AUDIT}
      table={fakeController<AstroliftAuditEvent>({
        rows: AUDIT_EVENTS_LONG,
        totalCount: 1,
        sort: undefined,
      })}
    />
  ),
};

/** A row opens the detail sheet with the before/after diff. */
export const RowDetails: Story = {
  render: () => <AuditLogScreen {...AUDIT} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getAllByRole("button", { name: /^app\.env\.update/ })[0]);
    await expect(await within(document.body).findByRole("dialog")).toBeInTheDocument();
  },
};

/** The retention editor, seeded from the current window. */
export const RetentionEditor: Story = {
  render: () => <AuditLogScreen {...AUDIT} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Retention/ }));
    const dialog = within(await within(document.body).findByRole("dialog"));
    await expect(dialog.getByLabelText(/Keep audit events for/)).toHaveValue(365);
  },
};
