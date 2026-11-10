import { NextIntlClientProvider } from "next-intl";
import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import {
  NvkeOperatorInstallPlanImagePanel,
  type NvkeOperatorInstallPlanImagePanelProps,
} from "./NvkeOperatorInstallPlanImagePanel";
import { INSTALL_IMAGE_PROPS } from "./NvkeOperatorInstallPlanImagePanel.stories";

afterEach(cleanup);
const text = messages.nvke.installImage;
function mount(change: Partial<NvkeOperatorInstallPlanImagePanelProps> = {}) {
  const props = { ...INSTALL_IMAGE_PROPS, ...change };
  render(
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanImagePanel {...props} />
    </NextIntlClientProvider>
  );
  return props;
}
it("retains a lost-reply UUID, clears private input and recovers without replay", async () => {
  const onPrepare = vi.fn(async () => false),
    onReview = vi.fn(async () => true);
  mount({ artifacts: null, onPrepare, onReview });
  expect(onPrepare).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText(messages.nvke.installTree.artifactId), {
    target: { value: INSTALL_IMAGE_PROPS.artifacts!.id },
  });
  fireEvent.change(screen.getByLabelText(text.reference), {
    target: { value: "private.example.test/owned:v1" },
  });
  fireEvent.change(screen.getByLabelText(text.policy), { target: { value: "reviewed" } });
  fireEvent.click(screen.getByRole("button", { name: text.prepare }));
  await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled());
  expect(onPrepare).toHaveBeenCalledWith({
    artifactId: INSTALL_IMAGE_PROPS.artifacts!.id,
    reference: "private.example.test/owned:v1",
    policy: "reviewed",
    maximumBytes: "16777216",
    maximumCalls: "512",
    timeoutSeconds: "300",
  });
  expect(screen.getByLabelText(messages.nvke.installTree.artifactId)).toHaveValue(
    INSTALL_IMAGE_PROPS.artifacts!.id
  );
  expect(screen.queryByDisplayValue("private.example.test/owned:v1")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(messages.nvke.installTree.unconfirmed);
  fireEvent.click(screen.getByRole("button", { name: text.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledWith(INSTALL_IMAGE_PROPS.artifacts!.id));
  expect(onPrepare).toHaveBeenCalledTimes(1);
});
it.each([
  { canReview: false },
  { pending: true },
  { loading: true },
  { tree: null },
  { plan: null },
  { charts: null },
  { tree: { ...INSTALL_IMAGE_PROPS.tree!, chartArtifactVersion: 99 } },
  { authority: { ...INSTALL_IMAGE_PROPS.authority!, verificationCode: "UNVERIFIED" } },
  {
    operation: {
      ...INSTALL_IMAGE_PROPS.operation,
      version: INSTALL_IMAGE_PROPS.operation.version + 1,
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
it("hides image evidence from a different reviewed tree", () => {
  mount({ artifacts: { ...INSTALL_IMAGE_PROPS.artifacts!, treeDigest: "f".repeat(64) } });
  expect(screen.queryByText(INSTALL_IMAGE_PROPS.artifacts!.rootDigest)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent(text.unavailable);
});
it("never prepares automatically when restoring a retained identity", () => {
  const onPrepare = vi.fn(async () => true);
  mount({ artifacts: null, retainedArtifactId: INSTALL_IMAGE_PROPS.artifacts!.id, onPrepare });
  expect(onPrepare).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  expect(screen.queryByLabelText(text.reference)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: text.review })).toBeEnabled();
});
