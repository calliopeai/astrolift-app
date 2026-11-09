import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeOperatorInstallPlanPanel } from "./NvkeOperatorInstallPlanPanel";
import { INSTALL_PLAN_PROPS as p } from "./NvkeOperatorInstallPlanPanel.stories";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const t = messages.nvke.installPlan;
it("distinguishes reconstruction, log order, original failure and proposed actions", () => {
  render(<NvkeOperatorInstallPlanPanel {...p} />, { wrapper });
  expect(screen.getByText(t.reconstructed)).toBeVisible();
  expect(screen.getByText(t.evidenceHelp)).toBeVisible();
  expect(screen.getByText(t.skip)).toBeVisible();
  expect(screen.getAllByText(t.execute)).toHaveLength(2);
  expect(screen.getAllByText("9007199254740993")).toHaveLength(3);
  expect(screen.getByRole("button", { name: t.prepare })).toBeDisabled();
});
it.each(["permission", "version", "instance", "unverified", "error", "loading", "wrong-plan"])(
  "hides prior observations after %s invalidation",
  (kind) => {
    const props = { ...p };
    if (kind === "permission") props.canReview = false;
    if (kind === "version") props.operation = { ...p.operation, version: p.operation.version + 1 };
    if (kind === "instance") props.authority = { ...p.authority!, instanceId: p.plan!.id };
    if (kind === "unverified")
      props.authority = { ...p.authority!, verificationCode: "UNVERIFIED" };
    if (kind === "error") props.error = "Failed";
    if (kind === "loading") props.loading = true;
    if (kind === "wrong-plan") props.retainedPlanId = p.operation.id;
    render(<NvkeOperatorInstallPlanPanel {...props} />, { wrapper });
    expect(screen.queryByText("postgresql")).toBeNull();
    expect(screen.queryByText(p.plan!.plan.planDigest)).toBeNull();
  }
);
it("keeps the exact request text, clears it after lost reply and only offers explicit recovery", async () => {
  const onPrepare = vi.fn(async () => false),
    onReview = vi.fn(async () => true),
    onRetryPreparation = vi.fn(async () => true);
  render(
    <NvkeOperatorInstallPlanPanel
      {...p}
      plan={null}
      onPrepare={onPrepare}
      onReview={onReview}
      onRetryPreparation={onRetryPreparation}
    />,
    { wrapper }
  );
  const requestText = ` { "format": 1, "planId": "${p.plan!.id}", "request": {"private": "keep-exact"}, "maximumInputBytes": "1024" }\n`;
  fireEvent.change(screen.getByLabelText(t.planId), { target: { value: p.plan!.id } });
  fireEvent.change(screen.getByLabelText(t.privateRequest), { target: { value: requestText } });
  fireEvent.click(screen.getByRole("button", { name: t.prepare }));
  await waitFor(() =>
    expect(onPrepare).toHaveBeenCalledExactlyOnceWith({ planId: p.plan!.id, requestText })
  );
  await waitFor(() => expect(screen.getByText(t.unconfirmed)).toBeVisible());
  expect(screen.queryByLabelText(t.privateRequest)).toBeNull();
  expect(screen.queryByDisplayValue(/keep-exact/)).toBeNull();
  expect(screen.getByRole("button", { name: t.prepare })).toBeDisabled();
  expect(onReview).not.toHaveBeenCalled();
  expect(onRetryPreparation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: t.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledExactlyOnceWith(p.plan!.id));
  fireEvent.click(screen.getByRole("button", { name: t.retry }));
  await waitFor(() => expect(onRetryPreparation).toHaveBeenCalledExactlyOnceWith(p.plan!.id));
  expect(onPrepare).toHaveBeenCalledOnce();
});
it("clears private form state when caller scope changes", () => {
  const view = render(<NvkeOperatorInstallPlanPanel {...p} plan={null} />, { wrapper });
  fireEvent.change(screen.getByLabelText(t.privateRequest), { target: { value: "private" } });
  view.rerender(<NvkeOperatorInstallPlanPanel {...p} plan={null} scopeKey="another-actor" />);
  expect(screen.getByLabelText(t.privateRequest)).toHaveValue("");
});
it("delegates search and paging to independent server controllers", async () => {
  const setSearch = vi.fn(),
    next = vi.fn(),
    onPrepare = vi.fn(async () => true);
  render(
    <NvkeOperatorInstallPlanPanel
      {...p}
      onPrepare={onPrepare}
      itemTable={{ ...p.itemTable, setSearch, next, hasNext: true, nextCursor: "cursor" }}
    />,
    { wrapper }
  );
  fireEvent.change(screen.getAllByRole("searchbox")[0], { target: { value: "postgres" } });
  await waitFor(() => expect(setSearch).toHaveBeenCalledWith("postgres"));
  fireEvent.click(screen.getAllByRole("button", { name: messages.shared.pagination.older })[0]);
  expect(next).toHaveBeenCalledOnce();
  expect(onPrepare).not.toHaveBeenCalled();
});
it("lets another reviewer inspect but refuses private retry", () => {
  render(<NvkeOperatorInstallPlanPanel {...p} canRetry={false} />, { wrapper });
  expect(screen.getByRole("button", { name: t.review })).toBeEnabled();
  expect(screen.getByRole("button", { name: t.retry })).toBeDisabled();
});
