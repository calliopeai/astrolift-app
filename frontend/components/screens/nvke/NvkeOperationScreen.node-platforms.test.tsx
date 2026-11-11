import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { NvkeOperationScreen } from "./NvkeOperationScreen";
import { OPERATION_DETAIL } from "./fixtures";
import { InstallNodePlatforms } from "./NvkeOperationScreen.stories";
import { INSTALL_NODE_PLATFORMS_PROPS } from "./NvkeOperatorInstallPlanNodePlatformsPanel.stories";

const copy = en.nvke.installImmutable;
it.each([{ canAct: false }, { pending: true }, { error: "Operation source unavailable" }])(
  "applies the operation guard to every node platform control: %j",
  (guard) => {
    const onReview = vi.fn(async () => true);
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <NvkeOperationScreen
          {...OPERATION_DETAIL}
          {...InstallNodePlatforms.args}
          {...guard}
          operatorInstallPlanNodePlatforms={{ ...INSTALL_NODE_PLATFORMS_PROPS, onReview }}
        />
      </NextIntlClientProvider>
    );
    const region = within(
      screen.getByRole("heading", { name: en.nvke.installNodePlatforms.title }).closest("section")!
    );
    for (const action of [copy.prepare, copy.review, copy.retry]) {
      const button = region.getByRole("button", { name: action });
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(onReview).not.toHaveBeenCalled();
  }
);

it("reviews only the explicitly selected node artifact in the composed operation screen", async () => {
  const onReview = vi.fn(async () => true),
    onRetryPreparation = vi.fn(async () => true);
  render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <NvkeOperationScreen
        {...OPERATION_DETAIL}
        {...InstallNodePlatforms.args}
        operatorInstallPlanNodePlatforms={{
          ...INSTALL_NODE_PLATFORMS_PROPS,
          onReview,
          onRetryPreparation,
        }}
      />
    </NextIntlClientProvider>
  );
  const region = within(
    screen.getByRole("heading", { name: en.nvke.installNodePlatforms.title }).closest("section")!
  );
  expect(onReview).not.toHaveBeenCalled();
  fireEvent.click(region.getByRole("button", { name: copy.review }));
  await waitFor(() =>
    expect(onReview).toHaveBeenCalledExactlyOnceWith(INSTALL_NODE_PLATFORMS_PROPS.artifacts!.id)
  );
  expect(onRetryPreparation).not.toHaveBeenCalled();
  expect(region.getByText(en.nvke.installNodePlatforms.closureRequired)).toBeTruthy();
});
