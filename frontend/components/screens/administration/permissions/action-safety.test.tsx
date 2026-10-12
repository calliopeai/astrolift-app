import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl as render } from "@/test/render-with-intl";

import { ActionSafetyView } from "./ActionSafetyView";
import {
  ACTION_APPROVAL_REQUESTS_FIXTURE,
  ACTION_SAFETY_FIXTURE,
  SCOPED_SAFETY_FIXTURE,
} from "./fixtures";

vi.mock("@/components/shell/ShellHeader", () => ({
  ShellHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const base = {
  rows: ACTION_SAFETY_FIXTURE,
  inspecting: null,
  onInspect: () => {},
  loading: false,
  error: null,
  onRetry: () => {},
  saving: null,
  saveError: null,
  onSetRule: async () => {},
  requests: ACTION_APPROVAL_REQUESTS_FIXTURE,
  requestsError: null,
  deciding: null,
  decideError: null,
  onDecide: async () => {},
  scoped: SCOPED_SAFETY_FIXTURE,
};

describe("Action safety (#2203)", () => {
  it("shows each gate and sends only the switch that changed", () => {
    const onSetRule = vi.fn(async () => {});
    render(<ActionSafetyView {...base} onSetRule={onSetRule} />);

    const deregister = screen.getByRole("row", { name: /Deregister an app/ });
    expect(within(deregister).getByText("On")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Ask before: Deregister an app" }));
    expect(onSetRule).toHaveBeenLastCalledWith("app.deregister", { enabled: false });

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Require approval: Decommission a cluster" })
    );
    expect(onSetRule).toHaveBeenLastCalledWith("cluster.decommission", { requireApproval: true });
  });

  it("offers approval only on destructive deletes, with a quorum once it is on", () => {
    render(<ActionSafetyView {...base} />);
    const deregister = screen.getByRole("row", { name: /Deregister an app/ });
    expect(within(deregister).getByText("Required")).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Approvers needed: Deregister an app" })
    ).toHaveTextContent("2 approvers");
    expect(
      screen.queryByRole("combobox", { name: "Approvers needed: Decommission a cluster" })
    ).toBeNull();
    // Self-approval only means something once approval is on.
    expect(
      screen.getByRole("checkbox", { name: "Admins may approve their own: Decommission a cluster" })
    ).toBeDisabled();

    const deploy = screen.getByRole("row", { name: /Deploy to production/ });
    expect(
      within(deploy).getByText("Set per environment, under required approvals")
    ).toBeInTheDocument();
    const change = screen.getByRole("row", { name: /Change a secret/ });
    expect(within(change).getByText("Set per app, under secret approval")).toBeInTheDocument();
    expect(within(change).getByText("Also set for a team, app or person")).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Require approval: Change a secret" })
    ).toBeNull();
  });

  it("lets others decide a request and the requester withdraw theirs", () => {
    const onDecide = vi.fn(async () => {});
    render(<ActionSafetyView {...base} onDecide={onDecide} />);

    const theirs = screen.getByRole("listitem", { name: "Deregister an app: billing-api" });
    expect(within(theirs).getByText(/Requested by dana@example.com/)).toBeInTheDocument();
    fireEvent.click(within(theirs).getByRole("button", { name: "Approve" }));
    expect(onDecide).toHaveBeenLastCalledWith(ACTION_APPROVAL_REQUESTS_FIXTURE[0]!.id, "approve");
    fireEvent.click(within(theirs).getByRole("button", { name: "Reject" }));
    expect(onDecide).toHaveBeenLastCalledWith(ACTION_APPROVAL_REQUESTS_FIXTURE[0]!.id, "reject");

    const mine = screen.getByRole("listitem", { name: "Decommission a cluster: staging-east" });
    expect(within(mine).getByText("Approved: run it again to go ahead")).toBeInTheDocument();
    expect(within(mine).queryByRole("button", { name: "Approve" })).toBeNull();
    fireEvent.click(within(mine).getByRole("button", { name: "Withdraw" }));
    expect(onDecide).toHaveBeenLastCalledWith(ACTION_APPROVAL_REQUESTS_FIXTURE[1]!.id, "cancel");
  });

  it("keeps a refused save, a refused decision and a failed read visible", () => {
    const { rerender } = render(<ActionSafetyView {...base} saveError="not saved" />);
    expect(screen.getByRole("alert")).toHaveTextContent("not saved");
    rerender(<ActionSafetyView {...base} decideError="another approver is needed" />);
    expect(screen.getByRole("alert")).toHaveTextContent("another approver is needed");
    rerender(<ActionSafetyView {...base} rows={null} error="Network error" requests={[]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Network error");
    expect(screen.getByText("No open requests.")).toBeInTheDocument();
  });

  it("shows what applies on one app read-only, and goes back to the organization", async () => {
    const onInspect = vi.fn();
    const { rerender } = render(<ActionSafetyView {...base} onInspect={onInspect} />);
    fireEvent.click(screen.getByRole("combobox", { name: "Show what applies on" }));
    fireEvent.click(await screen.findByRole("option", { name: "billing-api" }));
    expect(onInspect).toHaveBeenLastCalledWith("billing-api");

    rerender(<ActionSafetyView {...base} inspecting="billing-api" onInspect={onInspect} />);
    expect(screen.getByText(/Read-only: what applies on this app/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Ask before: Deregister an app" })).toBeDisabled();
    fireEvent.click(screen.getByRole("combobox", { name: "Show what applies on" }));
    fireEvent.click(await screen.findByRole("option", { name: "The whole organization" }));
    expect(onInspect).toHaveBeenLastCalledWith(null);
  });
});
