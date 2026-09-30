import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { MuteAlertRuleSheet } from "./alerts/MuteAlertRuleSheet";
import { RULES } from "./alerts/alerts.fixtures";
import { ApprovalsQueueScreen } from "./approvals/ApprovalsQueue";
import { QUEUE } from "./approvals/approvals-a.fixtures";
import { InvitationExpiryBadge, formatRemaining } from "./members/InvitationExpiryBadge";

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

function Context({ children }: { children: ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      {children}
    </NextIntlClientProvider>
  );
}

describe("approval selection after polling", () => {
  it("counts visible selections and selects new rows even when stale ids remain", () => {
    const view = render(
      <Context>
        <ApprovalsQueueScreen {...QUEUE} pending={QUEUE.pending.slice(0, 2)} />
      </Context>
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all pending" }));
    expect(screen.getByText("2 of 2 selected")).toBeVisible();
    view.rerender(
      <Context>
        <ApprovalsQueueScreen {...QUEUE} pending={QUEUE.pending.slice(1)} />
      </Context>
    );
    expect(screen.getByText("1 of 2 selected")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Select all pending" })).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all pending" }));
    expect(screen.getByText("2 of 2 selected")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Select all pending" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all pending" }));
    expect(screen.getByText("0 of 2 selected")).toBeVisible();
  });
});

describe("unknown invitation expiry", () => {
  it("renders unavailable dates without formatting an invalid Date or inventing a countdown", () => {
    render(<InvitationExpiryBadge expiresAt="invalid-date" />, { wrapper: Context });
    expect(screen.getByText("—")).toBeVisible();
    expect(screen.queryByText(/NaN|expired|expires in/)).not.toBeInTheDocument();
  });
  it.each([Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
    "does not invent a deadline for nonfinite remaining time %s",
    (duration) => {
      const translate = vi.fn(() => "expiry");
      expect(formatRemaining(duration, translate)).toBe("—");
      expect(translate).not.toHaveBeenCalled();
    }
  );
});

describe("mute duration admission", () => {
  function setup(hours: string, busy = false) {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <MuteAlertRuleSheet
        target={RULES[0]}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
        busy={busy}
      />,
      { wrapper: Context }
    );
    const duration = screen.getByLabelText("Duration (hours)");
    fireEvent.change(duration, { target: { value: hours } });
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: " reviewed " } });
    fireEvent.submit(duration.closest("form")!);
    return onSubmit;
  }
  it.each(["0", "0.5", "1.5", "169", ""])(
    "refuses %s even when browser constraint validation is bypassed",
    (hours) => expect(setup(hours)).not.toHaveBeenCalled()
  );
  it.each(["1", "168"])("accepts the %s hour boundary", (hours) => {
    expect(setup(hours)).toHaveBeenCalledExactlyOnceWith(Number(hours) * 3600, "reviewed");
  });
  it("does not submit again while the first mute is pending", () => {
    expect(setup("2", true)).not.toHaveBeenCalled();
  });
});
