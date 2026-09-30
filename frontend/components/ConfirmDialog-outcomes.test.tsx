import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { ConfirmDialog } from "./ConfirmDialog";
const diagnostic = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: diagnostic } }));
beforeEach(() => diagnostic.mockClear());
function Frame({ action }: { action: (reason: string) => unknown }) {
  const [open, setOpen] = useState(true);
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Delete the selected record?"
        reason={{ label: "Audit reason" }}
        onConfirm={action}
      />
    </NextIntlClientProvider>
  );
}
describe("confirmation outcomes", () => {
  it("retains reason after pending false, avoids duplicate diagnostics, and allows an explicit retry", async () => {
    let complete!: (result: boolean) => void;
    const action = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<boolean>((resolve) => {
            complete = resolve;
          })
      )
      .mockResolvedValueOnce(true);
    render(<Frame action={action} />);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(action).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "Audit reason" }), {
      target: { value: "  actual audit reason  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("textbox")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Working…" }));
    expect(action).toHaveBeenCalledExactlyOnceWith("actual audit reason");
    await act(async () => {
      diagnostic("actual caller diagnostic");
      complete(false);
    });
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("  actual audit reason  ");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
    expect(diagnostic).toHaveBeenCalledExactlyOnceWith("actual caller diagnostic");
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(action).toHaveBeenNthCalledWith(2, "actual audit reason");
    expect(diagnostic).toHaveBeenCalledTimes(1);
  });
  it("retains synchronous false without inventing a diagnostic", async () => {
    const action = vi.fn(() => false);
    render(<Frame action={action} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "reason" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled());
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(diagnostic).not.toHaveBeenCalled();
  });
  it.each([undefined, null, 0, "", true])(
    "preserves successful resolved %s callers",
    async (result) => {
      render(<Frame action={() => Promise.resolve(result)} />);
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "reason" } });
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(diagnostic).not.toHaveBeenCalled();
    }
  );
});
