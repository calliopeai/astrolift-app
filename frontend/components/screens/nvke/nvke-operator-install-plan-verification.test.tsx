import { NextIntlClientProvider } from "next-intl";
import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import {
  NvkeOperatorInstallPlanVerificationPanel,
  type NvkeOperatorInstallPlanVerificationPanelProps,
} from "./NvkeOperatorInstallPlanVerificationPanel";
import { INSTALL_VERIFICATION_PROPS } from "./NvkeOperatorInstallPlanVerificationPanel.stories";

afterEach(cleanup);
const text = messages.nvke.installVerification;
function mount(change: Partial<NvkeOperatorInstallPlanVerificationPanelProps> = {}) {
  const props = { ...INSTALL_VERIFICATION_PROPS, ...change };
  render(
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanVerificationPanel {...props} />
    </NextIntlClientProvider>
  );
  return props;
}

it("retains a lost-reply UUID and offers only explicit metadata recovery", async () => {
  const onPrepare = vi.fn(async () => false),
    onReview = vi.fn(async () => true);
  const props = mount({ artifacts: null, onPrepare, onReview });
  fireEvent.change(screen.getByLabelText(messages.nvke.installTree.artifactId), {
    target: { value: INSTALL_VERIFICATION_PROPS.artifacts!.id },
  });
  fireEvent.click(screen.getByRole("button", { name: text.prepare }));
  await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled());
  expect(screen.getByLabelText(messages.nvke.installTree.artifactId)).toHaveValue(
    INSTALL_VERIFICATION_PROPS.artifacts!.id
  );
  expect(screen.getByRole("alert")).toHaveTextContent(messages.nvke.installTree.unconfirmed);
  expect(onPrepare).toHaveBeenCalledWith({
    artifactId: INSTALL_VERIFICATION_PROPS.artifacts!.id,
    maximumBytes: "16777216",
    maximumCaptureAgeSeconds: "3600",
    maximumCalls: "512",
    timeoutSeconds: "300",
  });
  fireEvent.click(screen.getByRole("button", { name: text.review }));
  await waitFor(() =>
    expect(onReview).toHaveBeenCalledWith(INSTALL_VERIFICATION_PROPS.artifacts!.id)
  );
  expect(onPrepare).toHaveBeenCalledTimes(1);
  expect(screen.getByText(props.context!.id)).toBeInTheDocument();
});

it.each([
  { canReview: false },
  { pending: true },
  { loading: true },
  { context: null },
  { contextSelection: { item: "controller", ordinal: 2 } },
  {
    operation: {
      ...INSTALL_VERIFICATION_PROPS.operation,
      version: INSTALL_VERIFICATION_PROPS.operation.version + 1,
    },
  },
])("refuses callbacks for unavailable source or authority %j", (change) => {
  const onPrepare = vi.fn(async () => true),
    onReview = vi.fn(async () => true);
  mount({ ...change, onPrepare, onReview });
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  expect(screen.getByRole("button", { name: text.review })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: text.review }));
  expect(onPrepare).not.toHaveBeenCalled();
  expect(onReview).not.toHaveBeenCalled();
});

it("lets another reviewer inspect metadata but disables private retry", () => {
  const onRetryPreparation = vi.fn(async () => true);
  mount({ canRetry: false, onRetryPreparation });
  expect(screen.getByRole("button", { name: text.review })).toBeEnabled();
  expect(screen.getByRole("button", { name: text.retry })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: text.retry }));
  expect(onRetryPreparation).not.toHaveBeenCalled();
});

it("hides an observation bound to another context", () => {
  mount({ artifacts: { ...INSTALL_VERIFICATION_PROPS.artifacts!, contextDigest: "f".repeat(64) } });
  expect(
    screen.queryByText(INSTALL_VERIFICATION_PROPS.artifacts!.verificationDigest)
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent(text.unavailable);
});
