import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { ReviewedStartSheet, type ReviewedStartSheetProps } from "./ReviewedStartSheet";
import type { InputContract } from "./reviewed-start-model";
const contract: InputContract = {
  schema: { type: "object", properties: {}, additionalProperties: false },
  digest: "schema",
  supported: true,
  error: "",
  acceptsInputs: false,
  supportsSimpleForm: true,
  fields: [],
};
function view(overrides: Partial<ReviewedStartSheetProps> = {}) {
  const onSubmit = vi.fn(async () => {});
  const props: ReviewedStartSheetProps = {
    kind: "workflow",
    open: true,
    loading: false,
    submitting: false,
    review: {
      id: "exact-guid",
      name: "Disposable workflow",
      revision: "reviewed-revision",
      enabled: true,
      contract,
    },
    stamp: null,
    receipt: null,
    error: null,
    uncertain: false,
    restored: false,
    onClose: vi.fn(),
    onReconcile: vi.fn(async () => {}),
    onSubmit,
    ...overrides,
  };
  render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ReviewedStartSheet {...props} />
    </NextIntlClientProvider>
  );
  return onSubmit;
}
describe("reviewed workflow start form", () => {
  it("requires explicit confirmation and sends an explicit empty object for no-input definitions", async () => {
    const submit = view();
    const start = screen.getByRole("button", { name: "Start reviewed run" });
    expect(start).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(start);
    await waitFor(() => expect(submit).toHaveBeenCalledWith({}, ""));
  });
  it("does not start disabled or unsupported definitions", () => {
    view({
      review: {
        id: "guid",
        name: "Unsupported",
        revision: "rev",
        enabled: true,
        contract: { ...contract, supported: false, schema: null, error: "Unsupported contract" },
      },
    });
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start reviewed run" })).toBeDisabled();
  });
  it("refuses invalid complex JSON before dispatch", async () => {
    const submit = view({
      review: {
        id: "guid",
        name: "Complex",
        revision: "rev",
        enabled: true,
        contract: { ...contract, acceptsInputs: true, supportsSimpleForm: false },
      },
    });
    fireEvent.change(screen.getByLabelText("Run inputs (JSON object)"), {
      target: { value: "[]" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Start reviewed run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter valid inputs");
    expect(submit).not.toHaveBeenCalled();
  });
  it("keeps stale uncertain starts in reconciliation without creating a replacement", () => {
    view({
      uncertain: true,
      stamp: {
        kind: "workflow",
        targetId: "exact-guid",
        requestId: "original-key",
        revision: "old-revision",
        inputSchemaDigest: "schema",
      },
    });
    expect(screen.getByRole("button", { name: "Retry same request" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check status" })).toBeEnabled();
    expect(screen.getByText("original-key")).toBeVisible();
  });
  it("shows engine acceptance as a separate state from completion", () => {
    view({
      receipt: {
        id: "execution",
        temporalWorkflowId: "workflow",
        temporalRunId: "exact-run",
        dispatchStatus: "submitted",
      },
    });
    expect(screen.getByRole("status")).toHaveTextContent("This does not mean the run has finished");
    expect(screen.queryByRole("button", { name: "Start reviewed run" })).toBeNull();
  });
});
