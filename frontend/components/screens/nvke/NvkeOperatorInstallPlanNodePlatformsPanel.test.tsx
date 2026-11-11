import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import {
  NvkeOperatorInstallPlanNodePlatformsPanel,
  type NvkeOperatorInstallPlanNodePlatformsPanelProps,
} from "./NvkeOperatorInstallPlanNodePlatformsPanel";
import { INSTALL_NODE_PLATFORMS_PROPS as fixture } from "./NvkeOperatorInstallPlanNodePlatformsPanel.stories";

const text = en.nvke.installImmutable,
  nodeText = en.nvke.installNodePlatforms;
const id = "db84f977-f607-4afc-bbc8-58ea3fa78c2f";
const privateScope = JSON.stringify({
  "private-node": {
    uid: "private-uid",
    runtimeProfile: "containerd/v2.3.0",
    platform: { os: "linux", architecture: "arm64" },
  },
});
function panel(props: NvkeOperatorInstallPlanNodePlatformsPanelProps) {
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <NvkeOperatorInstallPlanNodePlatformsPanel {...props} />
    </NextIntlClientProvider>
  );
}
it("shows the validated observation and leaves installation admission unavailable", () => {
  render(panel(fixture));
  expect(screen.getByText(fixture.artifacts!.platformDigest)).toBeTruthy();
  expect(screen.getByText(nodeText.closureRequired)).toBeTruthy();
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  expect(screen.queryByRole("button", { name: /execute|continue|install now/i })).toBeNull();
});
it("clears private scope after a lost response and requires explicit review", async () => {
  const onPrepare = vi.fn(async () => false),
    onReview = vi.fn(async () => true),
    onRetryPreparation = vi.fn(async () => true);
  render(panel({ ...fixture, artifacts: null, onPrepare, onReview, onRetryPreparation }));
  fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText(nodeText.nodePlatforms), {
    target: { value: privateScope },
  });
  fireEvent.click(screen.getByRole("button", { name: text.prepare }));
  await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByText(en.nvke.installTree.unconfirmed)).toBeTruthy());
  expect(screen.getByLabelText(en.nvke.installTree.artifactId)).toHaveValue(id);
  expect(screen.queryByDisplayValue(privateScope)).toBeNull();
  expect(onReview).not.toHaveBeenCalled();
  expect(onRetryPreparation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: text.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledWith(id));
  expect(onPrepare).toHaveBeenCalledTimes(1);
});
it.each([
  { binding: null },
  { canReview: false },
  { pending: true },
  { loading: true },
  { authority: { ...fixture.authority!, verificationCode: "UNVERIFIED" } },
  { operation: { ...fixture.operation, version: fixture.operation.version + 1 } },
])("disables controls for an unavailable or busy reviewed source", (change) => {
  render(panel({ ...fixture, ...change }));
  for (const name of [text.prepare, text.review, text.retry])
    expect(screen.getByRole("button", { name })).toBeDisabled();
});
it("hides a saved observation after the binding version changes", () => {
  render(
    panel({ ...fixture, binding: { ...fixture.binding!, version: fixture.binding!.version + 1 } })
  );
  expect(screen.queryByText(fixture.artifacts!.platformDigest)).toBeNull();
  expect(screen.getByText(text.unavailable)).toBeTruthy();
});
it("does not allow another reviewer to retry the retained private intent", () => {
  render(panel({ ...fixture, canRetry: false }));
  expect(screen.getByRole("button", { name: text.retry })).toBeDisabled();
  expect(screen.getByRole("button", { name: text.review })).not.toBeDisabled();
});
it("retains the selected identity and clears private scope after a thrown callback", async () => {
  const onPrepare = vi.fn(async () => {
    throw new Error("PRIVATE");
  });
  render(panel({ ...fixture, artifacts: null, onPrepare }));
  fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText(nodeText.nodePlatforms), {
    target: { value: privateScope },
  });
  fireEvent.click(screen.getByRole("button", { name: text.prepare }));
  await waitFor(() => expect(screen.getByText(en.nvke.installTree.unconfirmed)).toBeTruthy());
  expect(screen.queryByText("PRIVATE")).toBeNull();
  expect(screen.queryByDisplayValue(privateScope)).toBeNull();
  expect(screen.getByLabelText(en.nvke.installTree.artifactId)).toHaveValue(id);
});

it("admits only one preparation callback while asynchronous form submissions overlap", async () => {
  let finish: (value: boolean) => void = () => {
    throw new Error("Preparation has not started");
  };
  const onPrepare = vi.fn(
    () =>
      new Promise<boolean>((resolve) => {
        finish = resolve;
      })
  );
  render(panel({ ...fixture, artifacts: null, onPrepare }));
  fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText(nodeText.nodePlatforms), {
    target: { value: privateScope },
  });
  const form = screen.getByRole("button", { name: text.prepare }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  await act(async () => finish(false));
  await waitFor(() => expect(screen.getByText(en.nvke.installTree.unconfirmed)).toBeTruthy());
  expect(onPrepare).toHaveBeenCalledTimes(1);
});
