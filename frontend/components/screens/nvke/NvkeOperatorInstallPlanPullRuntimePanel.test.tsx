import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import {
  NvkeOperatorInstallPlanPullRuntimePanel,
  type NvkeOperatorInstallPlanPullRuntimePanelProps,
} from "./NvkeOperatorInstallPlanPullRuntimePanel";
import {
  INSTALL_PULL_RUNTIME_PROPS as fixture,
  PULL_RUNTIME_RESULT,
} from "./NvkeOperatorInstallPlanPullRuntimePanel.stories";

const copy = en.nvke.installPullRuntime;
const element = (props: Partial<NvkeOperatorInstallPlanPullRuntimePanelProps> = {}) => (
  <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
    <NvkeOperatorInstallPlanPullRuntimePanel {...fixture} {...props} />
  </NextIntlClientProvider>
);
it("reads only on explicit action and never exposes prepare or retry controls", async () => {
  const onReview = vi.fn(async () => true);
  render(element({ onReview }));
  expect(onReview).not.toHaveBeenCalled();
  expect(screen.getAllByRole("button")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: copy.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledExactlyOnceWith());
  expect(screen.getByText(copy.closureRequired)).toBeTruthy();
});
it.each([
  { canReview: false },
  { pending: true },
  { loading: true },
  { analysis: null },
  { nodePlatforms: null },
  { operation: { ...fixture.operation, version: fixture.operation.version + 1 } },
  { authority: { ...fixture.authority!, version: fixture.authority!.version + 1 } },
  {
    nodePlatforms: {
      ...fixture.nodePlatforms!,
      bindingVersion: fixture.nodePlatforms!.bindingVersion + 1,
    },
  },
  { analysis: { ...fixture.analysis!, pullAuthorityDigest: "0".repeat(64) } },
])("refuses unavailable or stale reviewed source: %j", (change) => {
  const onReview = vi.fn(async () => true);
  render(element({ ...change, onReview, correspondence: PULL_RUNTIME_RESULT }));
  expect(screen.getAllByRole("button")[0]).toBeDisabled();
  expect(screen.queryByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeNull();
  fireEvent.click(screen.getAllByRole("button")[0]);
  expect(onReview).not.toHaveBeenCalled();
});
it("hides a formerly matched result when either selected parent changes", () => {
  const view = render(element({ correspondence: PULL_RUNTIME_RESULT }));
  expect(screen.getByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeTruthy();
  view.rerender(
    element({
      correspondence: PULL_RUNTIME_RESULT,
      analysis: { ...fixture.analysis!, version: fixture.analysis!.version + 1 },
    })
  );
  expect(screen.queryByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeNull();
  view.rerender(
    element({
      correspondence: PULL_RUNTIME_RESULT,
      nodePlatforms: { ...fixture.nodePlatforms!, version: fixture.nodePlatforms!.version + 1 },
    })
  );
  expect(screen.queryByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeNull();
});
it("keeps a lost read unconfirmed until another explicit review", async () => {
  const onReview = vi.fn(async () => false);
  render(element({ correspondence: PULL_RUNTIME_RESULT, onReview }));
  fireEvent.click(screen.getByRole("button", { name: copy.review }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(copy.unconfirmed));
  expect(screen.queryByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeNull();
  expect(onReview).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: copy.review })).toBeEnabled();
});
it("does not duplicate an in-flight review", async () => {
  let finish!: (value: boolean) => void;
  const onReview = vi.fn(
    () =>
      new Promise<boolean>((resolve) => {
        finish = resolve;
      })
  );
  render(element({ onReview }));
  const button = screen.getByRole("button", { name: copy.review });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(onReview).toHaveBeenCalledTimes(1);
  expect(button).toBeDisabled();
  finish(true);
  await waitFor(() => expect(button).toBeEnabled());
});
it("refuses a response claiming continuation", () => {
  render(
    element({ correspondence: { ...PULL_RUNTIME_RESULT, continuationAuthorized: true } as never })
  );
  expect(screen.queryByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeNull();
});
