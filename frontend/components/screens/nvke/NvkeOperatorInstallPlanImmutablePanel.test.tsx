import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import {
  NvkeOperatorInstallPlanImmutablePanel,
  type NvkeOperatorInstallPlanImmutablePanelProps,
} from "./NvkeOperatorInstallPlanImmutablePanel";
import {
  INSTALL_IMMUTABLE_PAYLOAD_PROPS,
  INSTALL_IMMUTABLE_EXECUTION_PROPS,
} from "./NvkeOperatorInstallPlanImmutablePanel.stories";

function panel(props: NvkeOperatorInstallPlanImmutablePanelProps) {
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <NvkeOperatorInstallPlanImmutablePanel {...props} />
    </NextIntlClientProvider>
  );
}
const id = "70cd7727-bf6a-4c5d-b6cb-24c8fd063271";
const text = en.nvke.installImmutable;
it.each([INSTALL_IMMUTABLE_PAYLOAD_PROPS, INSTALL_IMMUTABLE_EXECUTION_PROPS])(
  "renders the validated $mode artifact and leaves native continuation unavailable",
  (props) => {
    render(panel(props));
    expect(screen.getAllByText(props.artifacts!.id).length).toBeGreaterThan(0);
    expect(screen.getByText(text.closureRequired)).toBeTruthy();
    expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /continue|execute|install now/i })).toBeNull();
  }
);
it.each([INSTALL_IMMUTABLE_PAYLOAD_PROPS, INSTALL_IMMUTABLE_EXECUTION_PROPS])(
  "retains the $mode UUID after a lost response without automatic replay",
  async (base) => {
    const onPrepare = vi.fn(async () => false),
      onReview = vi.fn(async () => true),
      onRetryPreparation = vi.fn(async () => true);
    const props = { ...base, artifacts: null, onPrepare, onReview, onRetryPreparation };
    render(panel(props));
    fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
      target: { value: id },
    });
    let privateSelection: string;
    if (base.mode === "payload") {
      privateSelection = "NKTP_PrivatePhase";
      fireEvent.change(screen.getByLabelText(text.item), { target: { value: privateSelection } });
    } else {
      privateSelection = JSON.stringify(
        Object.fromEntries(
          Array.from({ length: base.plan!.itemCount }, (_, i) => [
            `NKTP_PrivatePhase_${i}`,
            {
              artifactId: `f0000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
              artifactDigest: "9".repeat(64),
            },
          ])
        )
      );
      fireEvent.change(screen.getByLabelText(text.payloadBindings), {
        target: { value: privateSelection },
      });
    }
    fireEvent.click(screen.getByRole("button", { name: text.prepare }));
    await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(en.nvke.installTree.unconfirmed)).toBeTruthy());
    expect(screen.getByLabelText(en.nvke.installTree.artifactId)).toHaveValue(id);
    expect(screen.queryByDisplayValue(privateSelection)).toBeNull();
    expect(onReview).not.toHaveBeenCalled();
    expect(onRetryPreparation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: text.review }));
    await waitFor(() => expect(onReview).toHaveBeenCalledWith(id));
    expect(onPrepare).toHaveBeenCalledTimes(1);
  }
);
it("disables preparation and recovery when the reviewed binding is unavailable", () => {
  const onPrepare = vi.fn(async () => true),
    onReview = vi.fn(async () => true);
  render(
    panel({
      ...INSTALL_IMMUTABLE_PAYLOAD_PROPS,
      binding: null,
      artifacts: null,
      onPrepare,
      onReview,
    })
  );
  for (const name of [text.prepare, text.review, text.retry])
    expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(screen.getByText(text.parentRequired)).toBeTruthy();
  expect(onPrepare).not.toHaveBeenCalled();
  expect(onReview).not.toHaveBeenCalled();
});
it("hides an artifact whose binding version changed", () => {
  const props = INSTALL_IMMUTABLE_PAYLOAD_PROPS;
  render(panel({ ...props, binding: { ...props.binding!, version: props.binding!.version + 1 } }));
  expect(screen.queryByText(props.artifacts!.artifactDigest)).toBeNull();
  expect(screen.getByText(text.unavailable)).toBeTruthy();
});
