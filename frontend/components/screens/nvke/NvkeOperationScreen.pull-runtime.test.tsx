import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { NvkeOperationScreen } from "./NvkeOperationScreen";
import { OPERATION_DETAIL } from "./fixtures";
import { InstallPullRuntime } from "./NvkeOperationScreen.stories";
import {
  INSTALL_PULL_RUNTIME_PROPS,
  PULL_RUNTIME_RESULT,
} from "./NvkeOperatorInstallPlanPullRuntimePanel.stories";
const copy = en.nvke.installPullRuntime;
it.each([{ canAct: false }, { pending: true }, { error: "Source unavailable" }])(
  "applies operation guards to pull correspondence review: %j",
  (guard) => {
    const onReview = vi.fn(async () => true);
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <NvkeOperationScreen
          {...OPERATION_DETAIL}
          {...InstallPullRuntime.args}
          {...guard}
          operatorInstallPlanPullRuntime={{
            ...INSTALL_PULL_RUNTIME_PROPS,
            correspondence: PULL_RUNTIME_RESULT,
            onReview,
          }}
        />
      </NextIntlClientProvider>
    );
    const region = within(screen.getByRole("heading", { name: copy.title }).closest("section")!);
    const button = region.getByRole("button");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onReview).not.toHaveBeenCalled();
    expect(region.queryByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeNull();
  }
);
it("reviews selected captures without creating or resuming work", async () => {
  const onReview = vi.fn(async () => true),
    onResume = vi.fn(async () => true);
  render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <NvkeOperationScreen
        {...OPERATION_DETAIL}
        {...InstallPullRuntime.args}
        onResume={onResume}
        operatorInstallPlanPullRuntime={{ ...INSTALL_PULL_RUNTIME_PROPS, onReview }}
      />
    </NextIntlClientProvider>
  );
  const region = within(screen.getByRole("heading", { name: copy.title }).closest("section")!);
  expect(onReview).not.toHaveBeenCalled();
  fireEvent.click(region.getByRole("button", { name: copy.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledExactlyOnceWith());
  expect(onResume).not.toHaveBeenCalled();
  expect(region.getByText(copy.closureRequired)).toBeTruthy();
});
