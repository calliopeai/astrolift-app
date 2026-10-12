import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActionSafetyView } from "./ActionSafetyView";
import { ACTION_SAFETY_FIXTURE } from "./fixtures";

vi.mock("@/components/shell/ShellHeader", () => ({
  ShellHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const base = {
  rows: ACTION_SAFETY_FIXTURE,
  loading: false,
  error: null,
  onRetry: () => {},
  saving: null,
  saveError: null,
};

describe("Action safety (#2203)", () => {
  it("shows each gate and turns one off for the organization", () => {
    const onSetRule = vi.fn(async () => {});
    render(<ActionSafetyView {...base} onSetRule={onSetRule} />);

    const deregister = screen.getByRole("row", { name: /Deregister an app/ });
    expect(within(deregister).getByText("On")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Ask before: Deregister an app" }));
    expect(onSetRule).toHaveBeenCalledWith("app.deregister", {
      enabled: false,
      allowSelfApproval: true,
    });
  });

  it("says when a narrower scope adds to a gate and disables self-approval while a gate is off", () => {
    render(<ActionSafetyView {...base} onSetRule={async () => {}} />);
    const change = screen.getByRole("row", { name: /Change a secret/ });
    expect(within(change).getByText("Also set for a team, app or person")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Admins may approve their own: Reveal a secret" })
    ).toBeDisabled();
  });

  it("keeps a refused save and a failed read visible", () => {
    const { rerender } = render(
      <ActionSafetyView {...base} saveError="not saved" onSetRule={async () => {}} />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("not saved");
    rerender(
      <ActionSafetyView {...base} rows={null} error="Network error" onSetRule={async () => {}} />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Network error");
  });
});
