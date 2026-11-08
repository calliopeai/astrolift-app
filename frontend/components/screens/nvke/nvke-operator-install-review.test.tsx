import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeOperatorInstallReviewPanel } from "./NvkeOperatorInstallReviewPanel";
import { INSTALL_REVIEW_PROPS } from "./NvkeOperatorInstallReviewPanel.stories";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const j = messages.nvke.installReview;
it("requires explicit review and distinguishes failed install from current readiness", () => {
  const onReview = vi.fn(async () => true);
  render(<NvkeOperatorInstallReviewPanel {...INSTALL_REVIEW_PROPS} onReview={onReview} />, {
    wrapper,
  });
  expect(onReview).not.toHaveBeenCalled();
  expect(screen.getByText(j.originalUncertain)).toBeVisible();
  expect(screen.getAllByText("9007199254740993")).toHaveLength(2);
  expect(screen.getAllByText(j.unknown).length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: /continue/i })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: j.prepare }));
  expect(onReview).toHaveBeenCalledOnce();
});
it.each(["permission", "version", "instance", "expired", "error", "loading", "authority"])(
  "hides observations after %s invalidation",
  (kind) => {
    const p = { ...INSTALL_REVIEW_PROPS };
    if (kind === "permission") p.canReview = false;
    if (kind === "version") p.operation = { ...p.operation, version: p.operation.version + 1 };
    if (kind === "instance")
      p.authority = { ...p.authority!, instanceId: "123e4567-e89b-12d3-a456-426614174099" };
    if (kind === "expired") p.review = { ...p.review!, snapshotExpiresAt: "2020-01-01T00:00:00Z" };
    if (kind === "error") p.error = "failed";
    if (kind === "loading") p.loading = true;
    if (kind === "authority") p.authority = { ...p.authority!, verificationCode: "UNVERIFIED" };
    render(<NvkeOperatorInstallReviewPanel {...p} />, { wrapper });
    expect(screen.queryByText("lgtm")).toBeNull();
    expect(screen.queryByText(INSTALL_REVIEW_PROPS.review!.reviewDigest)).toBeNull();
  }
);
it("delegates server search and pagination without preparing another native observation", async () => {
  const next = vi.fn(),
    setSearch = vi.fn(),
    onReview = vi.fn(async () => true);
  const p = {
    ...INSTALL_REVIEW_PROPS,
    onReview,
    table: {
      ...INSTALL_REVIEW_PROPS.table,
      hasNext: true,
      nextCursor: "cursor",
      totalCount: 113,
      next,
      setSearch,
    },
  };
  render(<NvkeOperatorInstallReviewPanel {...p} />, { wrapper });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "lgtm" } });
  await waitFor(() => expect(setSearch).toHaveBeenCalledWith("lgtm"));
  fireEvent.click(screen.getByRole("button", { name: messages.shared.pagination.older }));
  expect(next).toHaveBeenCalledOnce();
  expect(onReview).not.toHaveBeenCalled();
});

it("shows the prepared review on the operation screen and hides it when screen access is lost", async () => {
  const { NvkeOperationScreen } = await import("./NvkeOperationScreen");
  const { OPERATION_DETAIL } = await import("./fixtures");
  const p = {
    ...OPERATION_DETAIL,
    operation: INSTALL_REVIEW_PROPS.operation,
    operatorInstallReview: INSTALL_REVIEW_PROPS,
    canAct: true,
    canResume: false,
  };
  const view = render(<NvkeOperationScreen {...p} />, { wrapper });
  expect(screen.getByText("lgtm")).toBeVisible();
  view.rerender(<NvkeOperationScreen {...p} canAct={false} />);
  expect(screen.queryByText("lgtm")).toBeNull();
  expect(screen.getByRole("button", { name: j.prepare })).toBeDisabled();
});
